"""Diario PostgreSQL de reproducción. Guarda identidad técnica, nunca URLs ni excepciones."""
import json
import re

from app.providers.stremio import ProviderError


def failure_code(error):
    """Clasificación fija: nunca persistir str(error), que puede contener un token."""
    import httpx
    if isinstance(error, ProviderError):return error.code
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):return 'network_timeout'
    if isinstance(error, httpx.HTTPError):return 'network_error'
    return 'internal_error'


def probe_cause(stderr):
    """Extrae solo una categoría de FFprobe; la salida privada se descarta."""
    text=(stderr or b'')[:65536].lower()
    if b'403' in text or b'401' in text:return 'upstream_rejected'
    if b'404' in text or b'410' in text:return 'upstream_missing'
    if b'timed out' in text or b'timeout' in text:return 'network_timeout'
    if any(token in text for token in (b'connection',b'network',b'input/output',b'end of file')):return 'network_error'
    if any(token in text for token in (b'invalid data',b'moov atom',b'could not find codec')):return 'probe_format_error'
    return 'video_probe_failed'


class PlaybackEvents:
    def __init__(self, store):self.store=store

    def begin(self, actor, source, attempt_id, playback_id, client):
        """La huella y el ranking sobreviven al vencimiento de media_source."""
        with self.store.connect() as conn:
            row=conn.execute('''INSERT INTO playback_attempt
                (attempt_id,playback_id,actor,client,content_id,video_id,provider,
                 source_fingerprint,rank_position,ranking_snapshot)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT(attempt_id) DO NOTHING RETURNING attempt_id''',
                (attempt_id,playback_id,actor,client,source['content_id'],source['video_id'],
                 source.get('provider','mediafusion'),source.get('fingerprint'),
                 source.get('rank_position'),json.dumps(source.get('ranking_snapshot',{})))).fetchone()
            if not row:
                owner=conn.execute('SELECT actor,video_id,source_fingerprint FROM playback_attempt WHERE attempt_id=%s',
                    (attempt_id,)).fetchone()
                if owner!=(actor,source['video_id'],source.get('fingerprint')):
                    raise ProviderError('invalid_attempt','El intento pertenece a otra reproducción.',409)
            conn.execute('''INSERT INTO playback_event(attempt_id,event_id,event,stage)
                VALUES (%s,'server:started','attempt_started','start') ON CONFLICT DO NOTHING''',(attempt_id,))

    def context(self, actor, attempt_id):
        with self.store.connect() as conn:
            row=conn.execute('''SELECT content_id,video_id,provider,source_fingerprint
                FROM playback_attempt WHERE attempt_id=%s AND actor=%s''',(attempt_id,actor)).fetchone()
        if not row:raise ProviderError('attempt_not_found','No se encontró ese intento de reproducción.',404)
        return dict(zip(('content_id','video_id','provider','fingerprint'),row))

    def record(self, actor, attempt_id, event_id, event, stage='player', **fields):
        """Idempotente: un reenvío de telemetría no cuenta como un segundo fallo."""
        # Los códigos solo son identificadores; no se admite texto libre del proveedor.
        code=fields.get('error_code')
        if code and not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',code):code='player_error'
        with self.store.connect() as conn:
            row=conn.execute('''INSERT INTO playback_event
                (attempt_id,event_id,event,stage,error_code,upstream_status,position_ms,
                 elapsed_ms,duration_ms,buffer_ms,switch_reason,next_fingerprint,
                 subtitle_key,subtitle_token,render_context,render_samples,render_lag_ms,render_total_lag_ms)
                SELECT attempt_id,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                FROM playback_attempt WHERE attempt_id=%s AND actor=%s
                ON CONFLICT DO NOTHING RETURNING id''',
                (event_id,event,stage,code,fields.get('upstream_status'),fields.get('position_ms'),
                 fields.get('elapsed_ms'),fields.get('duration_ms'),fields.get('buffer_ms'),
                 fields.get('switch_reason'),fields.get('next_fingerprint'),fields.get('subtitle_key'),
                 fields.get('subtitle_token'),fields.get('render_context'),fields.get('render_samples'),
                 fields.get('render_lag_ms'),fields.get('render_total_lag_ms'),attempt_id,actor)).fetchone()
        return bool(row)

    def stable(self, actor, attempt_id):
        """La recuperación solo se aprende tras un minuto confirmado, no al preparar."""
        with self.store.connect() as conn:
            row=conn.execute('''SELECT 1 FROM playback_event e JOIN playback_attempt a USING(attempt_id)
                WHERE a.attempt_id=%s AND a.actor=%s AND e.event='first_frame'
                  AND e.created_at <= now()-interval '60 seconds'
                  AND NOT EXISTS (SELECT 1 FROM playback_event failed
                      WHERE failed.attempt_id=a.attempt_id AND failed.event='error'
                        AND failed.created_at>now()-interval '60 seconds') ''',(attempt_id,actor)).fetchone()
        if not row:return False
        self.store.record_source_success(self.context(actor,attempt_id))
        with self.store.connect() as conn:
            conn.execute('UPDATE playback_attempt SET failure_learned=false,learned_error_code=NULL WHERE attempt_id=%s AND actor=%s',
                (attempt_id,actor))
        return True

    def learn_failure(self, actor, attempt_id, code):
        """Servidor y cliente comparten la misma marca para no penalizar dos veces."""
        from app.storage import HARD_SOURCE_ERRORS, REPEATED_SOURCE_ERRORS, TEMPORARY_SOURCE_ERRORS
        if code not in HARD_SOURCE_ERRORS | REPEATED_SOURCE_ERRORS | TEMPORARY_SOURCE_ERRORS:return False
        with self.store.connect() as conn:
            row=conn.execute('''UPDATE playback_attempt SET failure_learned=true,learned_error_code=%s
                WHERE attempt_id=%s AND actor=%s AND (NOT failure_learned OR
                    (%s AND (learned_error_code IS NULL OR NOT learned_error_code=ANY(%s))))
                RETURNING content_id,video_id,provider,source_fingerprint''',
                (code,attempt_id,actor,code in HARD_SOURCE_ERRORS,list(HARD_SOURCE_ERRORS))).fetchone()
            if not row:return False
            source=dict(zip(('content_id','video_id','provider','fingerprint'),row))
            # Si falla PostgreSQL, la transacción exterior libera la marca y se puede
            # reintentar. No se persiste una penalización ficticia ni un segundo fallo.
            return self.store.record_source_failure(source,code)

    def summary(self, days=7):
        """Indicadores por intento; preparar una URL no se cuenta como ver imagen."""
        with self.store.connect() as conn:
            rows=conn.execute('''WITH attempts AS (
                SELECT a.*, bool_or(e.event='first_frame') AS image,
                    bool_or(e.event='stable') AS stable,
                    bool_or(e.event='error') AS failed,
                    bool_or(e.event='source_switch' AND e.switch_reason='automatic') AS skipped,
                    bool_or(e.event='cancelled') AS cancelled,
                    max(e.elapsed_ms) FILTER (WHERE e.event='first_frame') AS startup_ms,
                    coalesce(sum(e.duration_ms) FILTER (WHERE e.event='buffering_end'),0) AS stalled_ms,
                    row_number() OVER (PARTITION BY a.actor,a.playback_id ORDER BY a.created_at,a.attempt_id) AS ordinal
                FROM playback_attempt a LEFT JOIN playback_event e USING(attempt_id)
                WHERE a.created_at>now()-make_interval(days => %s)
                GROUP BY a.attempt_id)
                SELECT provider,count(*),count(*) FILTER (WHERE image),count(*) FILTER (WHERE stable),
                    count(*) FILTER (WHERE failed),count(*) FILTER (WHERE skipped),
                    count(*) FILTER (WHERE cancelled AND NOT image AND NOT failed AND NOT skipped),
                    count(*) FILTER (WHERE ordinal=1 AND (image OR failed OR skipped)),
                    count(*) FILTER (WHERE ordinal=1 AND image),round(avg(startup_ms)),sum(stalled_ms)
                FROM attempts GROUP BY provider ORDER BY provider''',(days,)).fetchall()
        names=('provider','attempts','first_frames','stable_attempts','failed_attempts','automatic_switches',
               'cancelled_before_image','resolved_first_attempts','first_attempts_with_image','average_startup_ms','buffering_ms')
        with self.store.connect() as conn:
            measurements=conn.execute('''SELECT a.client,e.render_context,sum(e.render_samples),
                round(sum(e.render_total_lag_ms)::numeric / nullif(sum(e.render_samples),0)),max(e.render_lag_ms)
                FROM playback_event e JOIN playback_attempt a USING(attempt_id)
                WHERE e.event='subtitle_render' AND e.created_at>now()-make_interval(days => %s)
                GROUP BY a.client,e.render_context ORDER BY a.client,e.render_context''',(days,)).fetchall()
        return {'days':days,'providers':[dict(zip(names,row)) for row in rows],
                'subtitle_render':[dict(zip(('client','context','samples','average_lag_ms','max_lag_ms'),row))
                                   for row in measurements]}
