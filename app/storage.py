"""PostgreSQL como almacenamiento único. Configuración de proveedor cifrada."""
import json

import psycopg
from cryptography.fernet import Fernet

from app.providers.stremio import PROVIDER_ORDER
from app.settings import Settings
from app.stream_ranking import url_key

NEUTRAL_HEALTH = 50
# Los fallos del archivo mantienen su política; la disponibilidad aprende aparte
# y caduca pronto. Cancelación, saturación local y cambios de audio nunca penalizan.
HARD_SOURCE_ERRORS = frozenset({
    'content_mismatch','video_invalid','invalid_playlist','playlist_too_large',
    'unsupported_source','episode_not_identified','wrong_file','no_video_file','invalid_torrent','invalid_source',
    'audio_language_mismatch','archive_source'
})
REPEATED_SOURCE_ERRORS = frozenset({'cast_conversion_failed','piece_error','invalid_piece'})
TEMPORARY_SOURCE_ERRORS = frozenset({
    'video_probe_failed','probe_format_error','video_unavailable','video_truncated',
    'upstream_rejected','upstream_missing','network_error','network_timeout',
    'preparation_timeout','cast_prepare_timeout','cast_buffer_timeout','slow_source',
    'player_error','startup_timeout','playback_stalled','cast_player_error'
})


class Store:
    def __init__(self, settings: Settings):
        self.dsn = settings.database_url.get_secret_value()
        self.cipher = Fernet(settings.encryption_key.get_secret_value().encode())

    def connect(self):
        return psycopg.connect(self.dsn, connect_timeout=5)

    def subtitle_verdict(self, token):
        with self.connect() as conn:
            row = conn.execute('SELECT status,reason FROM subtitle_verdict WHERE token=%s', (token,)).fetchone()
        return {'status': row[0], 'reason': row[1]} if row else None

    def save_subtitle_verdict(self, token, video, subtitle_key, status, reason=''):
        """El token incluye revisión, edición, audio y hash del contenido del subtítulo."""
        with self.connect() as conn:
            conn.execute('''INSERT INTO subtitle_verdict(token,video_id,source_fingerprint,subtitle_key,status,reason)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(token) DO UPDATE
                SET status=EXCLUDED.status,reason=EXCLUDED.reason,created_at=now()''',
                (token, video['video_id'], video['fingerprint'], subtitle_key, status, reason))

    def health(self) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1

    def save_subtitle_binding(self, binding):
        """Persiste el resultado y su evidencia en la misma transacción."""
        fields = ('token','video_id','asset_key','audio_key','subtitle_sha256','subtitle_key',
                  'revision','verification','body','body_sha256')
        values = [json.dumps(binding[key]) if key == 'verification' else binding[key] for key in fields]
        with self.connect() as conn:
            conn.execute('''INSERT INTO subtitle_binding
                (token,video_id,asset_key,audio_key,subtitle_sha256,subtitle_key,revision,verification,body,body_sha256)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                ON CONFLICT(token) DO UPDATE SET verification=EXCLUDED.verification,
                    body=EXCLUDED.body,body_sha256=EXCLUDED.body_sha256''', values)

    def subtitle_binding(self, video_id, asset_key, audio_key, revision):
        with self.connect() as conn:
            row = conn.execute('''SELECT token,subtitle_key,verification,body,body_sha256
                FROM subtitle_binding WHERE video_id=%s AND asset_key=%s AND audio_key=%s AND revision=%s
                ORDER BY created_at DESC LIMIT 1''', (video_id,asset_key,audio_key,revision)).fetchone()
        return dict(zip(('token','subtitle_key','verification','body','body_sha256'),row)) if row else None

    def subtitle_binding_token(self, token):
        with self.connect() as conn:
            row = conn.execute('''SELECT token,subtitle_key,verification,body,body_sha256
                FROM subtitle_binding WHERE token=%s''', (token,)).fetchone()
        return dict(zip(('token','subtitle_key','verification','body','body_sha256'),row)) if row else None

    def provider(self, provider: str = "mediafusion") -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT encrypted_manifest, summary FROM provider_config WHERE provider = %s", (provider,)).fetchone()
        if not row:
            return None
        return {"provider": provider, "manifest_url": self.cipher.decrypt(row[0].encode()).decode(), "summary": row[1]}

    def configured_providers(self) -> list[dict]:
        """Devuelve las configuraciones en el orden de consulta, sin exponer los enlaces privados."""
        with self.connect() as conn:
            rows = conn.execute("SELECT provider, encrypted_manifest, summary FROM provider_config").fetchall()
        saved = {row[0]: row for row in rows}
        return [{"provider": key, "manifest_url": self.cipher.decrypt(saved[key][1].encode()).decode(),
                 "summary": saved[key][2]} for key in PROVIDER_ORDER if key in saved]

    def save_provider(self, manifest_url: str, summary: dict, actor: str, provider: str = "mediafusion") -> None:
        encrypted = self.cipher.encrypt(manifest_url.encode()).decode()
        with self.connect() as conn:
            conn.execute("""INSERT INTO provider_config(provider, encrypted_manifest, summary)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (provider) DO UPDATE SET encrypted_manifest=EXCLUDED.encrypted_manifest,
                summary=EXCLUDED.summary, updated_at=now()""", (provider, encrypted, json.dumps(summary)))
            # Guardado y auditoría son una sola transacción.
            conn.execute("INSERT INTO audit_event(actor, action) VALUES (%s, 'provider.configure')", (actor,))

    def forget_provider(self, provider: str, actor: str) -> bool:
        with self.connect() as conn:
            removed = conn.execute("DELETE FROM provider_config WHERE provider = %s", (provider,)).rowcount
            conn.execute("INSERT INTO audit_event(actor, action) VALUES (%s, 'provider.forget')", (actor,))
        return bool(removed)

    def record(self, actor: str, action: str, result: str = "ok") -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO audit_event(actor, action, result) VALUES (%s, %s, %s)", (actor, action, result))

    def karaoke_preferences(self) -> dict[tuple[str, str], str]:
        """Decisiones compartidas por la casa para cada canción y versión."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT song_id, video_id, status FROM karaoke_preference"
            ).fetchall()
        return {(row[0], row[1]): row[2] for row in rows}

    def set_karaoke_preference(self, song_id: str, video_id: str, status: str, actor: str) -> None:
        """Guarda la marca y su auditoría en una sola transacción."""
        with self.connect() as conn:
            if status == "favorite":
                conn.execute(
                    "DELETE FROM karaoke_preference WHERE song_id=%s AND status='favorite' AND video_id<>%s",
                    (song_id, video_id),
                )
            if status == "neutral":
                conn.execute(
                    "DELETE FROM karaoke_preference WHERE song_id=%s AND video_id=%s",
                    (song_id, video_id),
                )
            else:
                conn.execute(
                    """INSERT INTO karaoke_preference(song_id, video_id, status, actor)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT(song_id, video_id) DO UPDATE
                       SET status=excluded.status, actor=excluded.actor, updated_at=now()""",
                    (song_id, video_id, status, actor),
                )
            conn.execute(
                "INSERT INTO audit_event(actor, action) VALUES (%s, %s)",
                (actor, "karaoke.version." + status),
            )

    def record_metric(self, provider: str, event: str, start_ms: int | None = None, bitrate_kbps: int | None = None) -> None:
        """Métrica sin identificar el video ni el enlace: solo proveedor, evento y tiempo."""
        if provider not in PROVIDER_ORDER:
            return
        with self.connect() as conn:
            conn.execute("INSERT INTO playback_metric(provider, event, start_ms, bitrate_kbps) VALUES (%s, %s, %s, %s)",
                         (provider, event, start_ms, bitrate_kbps))

    def record_speed(self, host: str, bytes_per_second: int, provider: str | None = None) -> None:
        """Media móvil por servidor y proveedor, sin conservar enlaces privados.

        El servidor final es la señal precisa. El promedio del proveedor solo sirve
        cuando una URL firmada todavía no permite reconocer a qué CDN terminará.
        """
        if not host or bytes_per_second <= 0:
            return
        with self.connect() as conn:
            conn.execute("""INSERT INTO source_speed(host, samples, bytes_per_second)
                VALUES (%s, 1, %s)
                ON CONFLICT (host) DO UPDATE SET
                    samples = least(source_speed.samples + 1, 50),
                    bytes_per_second = (source_speed.bytes_per_second * 7 + EXCLUDED.bytes_per_second * 3) / 10,
                    updated_at = now()""", (host[:200], int(bytes_per_second)))
            if provider in PROVIDER_ORDER:
                conn.execute("""INSERT INTO provider_speed(provider, samples, bytes_per_second)
                    VALUES (%s, 1, %s)
                    ON CONFLICT (provider) DO UPDATE SET
                        samples = least(provider_speed.samples + 1, 100),
                        bytes_per_second = (provider_speed.bytes_per_second * 7 + EXCLUDED.bytes_per_second * 3) / 10,
                        updated_at = now()""", (provider, int(bytes_per_second)))

    def record_route(self, url: str, host: str) -> None:
        """Recuerda a qué servidor llevó un enlace tras seguir sus redirecciones."""
        if not url or not host:
            return
        with self.connect() as conn:
            conn.execute("""INSERT INTO source_route(url_hash, host) VALUES (%s, %s)
                ON CONFLICT (url_hash) DO UPDATE SET host=EXCLUDED.host, updated_at=now()""",
                (url_key(url), host[:200]))

    def host_speeds(self) -> dict[str, int]:
        """Caudal conocido por servidor. Lo que no se ha medido no puntúa."""
        with self.connect() as conn:
            rows = conn.execute("SELECT host, bytes_per_second FROM source_speed WHERE samples > 0 AND updated_at > now() - interval '24 hours'").fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def provider_speeds(self) -> dict[str, int]:
        """Respaldo de red por proveedor; tres muestras evitan premiar un único pico."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT provider, bytes_per_second FROM provider_speed
                WHERE samples >= 3 AND updated_at > now() - interval '6 hours'""").fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def link_speeds(self) -> dict[str, int]:
        """Caudal esperable de cada enlace ya visto, por el servidor al que lleva."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT r.url_hash, s.bytes_per_second FROM source_route r
                JOIN source_speed s ON s.host = r.host
                WHERE s.samples > 0 AND s.updated_at > now() - interval '24 hours'
                  AND r.updated_at > now() - interval '12 hours'""").fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def provider_health(self) -> dict[str, int]:
        """Salud 0-100 por proveedor sobre los últimos intentos. Sin datos suficientes, valor neutral."""
        with self.connect() as conn:
            rows = conn.execute("""SELECT provider,
                    count(*) FILTER (WHERE event IN ('prepared','first_frame','cast_started')) AS good,
                    count(*) FILTER (WHERE event IN ('error','buffering')) AS bad
                FROM (SELECT provider, event FROM playback_metric ORDER BY created_at DESC LIMIT 400) recent
                GROUP BY provider""").fetchall()
            # Cada intento nuevo aporta una sola señal observada. Preparar una URL
            # no se cuenta como imagen; una señal estable puede recuperar un fallo.
            observed=conn.execute('''SELECT provider,
                count(*) FILTER (WHERE event IN ('first_frame','stable')),
                count(*) FILTER (WHERE event='error') FROM (
                    SELECT DISTINCT ON(a.attempt_id) a.provider,e.event
                    FROM playback_attempt a JOIN playback_event e USING(attempt_id)
                    WHERE a.created_at>now()-interval '7 days'
                      AND (e.event IN ('first_frame','stable') OR
                           (e.event='error' AND e.error_code=ANY(%s)))
                    ORDER BY a.attempt_id,e.created_at DESC,e.id DESC) outcomes
                GROUP BY provider''',(list(HARD_SOURCE_ERRORS|REPEATED_SOURCE_ERRORS|TEMPORARY_SOURCE_ERRORS),)).fetchall()
        actual={provider:(good,bad) for provider,good,bad in observed if good+bad>=5}
        rows=[(provider,*actual.get(provider,(good,bad))) for provider,good,bad in rows]
        present={row[0] for row in rows}
        rows.extend((provider,good,bad) for provider,(good,bad) in actual.items() if provider not in present)
        health = {key: NEUTRAL_HEALTH for key in PROVIDER_ORDER}
        for provider, good, bad in rows:
            total = good + bad
            # Menos de 5 intentos no mueve el ranking; la señal se acerca al valor real de forma gradual.
            if provider in health and total >= 5:
                confidence = min(1.0, total / 20)
                health[provider] = round(NEUTRAL_HEALTH + (good * 100 / total - NEUTRAL_HEALTH) * confidence)
        return health

    def source_reputation(self, video_id: str) -> dict[str, str]:
        """Estado vigente de cada edición; una sola falla repetible todavía es neutral."""
        with self.connect() as conn:
            rows=conn.execute("""SELECT source_fingerprint,
                    CASE WHEN audio_rejected_until>now() THEN 'audio_rejected'
                         WHEN archive_rejected_until>now() THEN 'archive_rejected'
                         WHEN hard_until>now() THEN 'hard'
                         WHEN failures>=2 AND last_failure_at>now()-interval '30 days' THEN 'repeated'
                         WHEN temporary_until>now() THEN 'temporary'
                         ELSE '' END
                FROM source_reputation WHERE video_id=%s""",(video_id,)).fetchall()
        return {fingerprint:status for fingerprint,status in rows if status}

    def record_source_failure(self, source: dict, code: str) -> bool:
        """Errores de disponibilidad bajan prioridad entre 15 y 60 minutos, sin excluir."""
        hard=code in HARD_SOURCE_ERRORS
        temporary=code in TEMPORARY_SOURCE_ERRORS
        if not hard and not temporary and code not in REPEATED_SOURCE_ERRORS:return False
        fingerprint=source.get('fingerprint')
        video_id,provider=source.get('video_id'),source.get('provider')
        if not fingerprint or not video_id or not provider:return False
        with self.connect() as conn:
            if temporary:
                conn.execute("""INSERT INTO source_reputation
                    (video_id,source_fingerprint,provider,temporary_failures,temporary_until)
                    VALUES (%s,%s,%s,1,now()+interval '15 minutes')
                    ON CONFLICT(video_id,source_fingerprint) DO UPDATE SET
                        temporary_failures=CASE WHEN source_reputation.temporary_until<now()
                            OR source_reputation.temporary_until IS NULL THEN 1
                            ELSE least(source_reputation.temporary_failures+1,4) END,
                        temporary_until=now()+interval '15 minutes' *
                            CASE WHEN source_reputation.temporary_until<now()
                                OR source_reputation.temporary_until IS NULL THEN 1
                                ELSE least(source_reputation.temporary_failures+1,4) END""",
                    (video_id,fingerprint,provider))
                return True
            conn.execute("""DELETE FROM source_reputation
                WHERE hard_until<now() AND last_failure_at<now()-interval '90 days'
                  AND (temporary_until IS NULL OR temporary_until<now())""")
            conn.execute("""INSERT INTO source_reputation
                    (video_id,source_fingerprint,provider,failures,last_failure_at,hard_until,audio_rejected_until,archive_rejected_until)
                    VALUES (%s,%s,%s,%s,now(),CASE WHEN %s THEN now()+interval '30 days' END,
                        CASE WHEN %s THEN now()+interval '30 days' END,
                        CASE WHEN %s THEN now()+interval '30 days' END)
                ON CONFLICT(video_id,source_fingerprint) DO UPDATE SET
                    provider=EXCLUDED.provider,
                    failures=CASE WHEN %s THEN source_reputation.failures
                        WHEN source_reputation.last_failure_at<now()-interval '30 days' THEN 1
                        ELSE least(source_reputation.failures+1,32767) END,
                    last_failure_at=now(),
                    hard_until=CASE WHEN %s THEN now()+interval '30 days'
                        ELSE source_reputation.hard_until END,
                    audio_rejected_until=CASE WHEN %s THEN now()+interval '30 days'
                        ELSE source_reputation.audio_rejected_until END,
                    archive_rejected_until=CASE WHEN %s THEN now()+interval '30 days'
                        ELSE source_reputation.archive_rejected_until END""",
                (video_id,fingerprint,provider,0 if hard else 1,hard,code=='audio_language_mismatch',code=='archive_source',
                 hard,hard,code=='audio_language_mismatch',code=='archive_source'))
        return True

    def record_source_success(self, source: dict) -> None:
        """Una reproducción estable limpia solo fallos transitorios, nunca audio/contenido."""
        with self.connect() as conn:
            conn.execute('''UPDATE source_reputation SET temporary_until=NULL,
                temporary_failures=0,last_success_at=now()
                WHERE video_id=%s AND source_fingerprint=%s''',
                (source['video_id'],source.get('fingerprint')))
