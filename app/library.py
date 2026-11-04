"""Favoritos y progreso. Una sesión nueva invalida actualizaciones de sesiones anteriores."""
import secrets
import json

from app.providers.stremio import ProviderError
from app.stream_ranking import runtime_seconds

MINIMUM_PROGRESS_MS = 30_000
COMPLETED_FRACTION = 0.92


def watched_fraction(data,position,duration,completed=False):
    """Cuánto se lleva visto, entre 0 y 1.

    La duración guardada es la del archivo, que es la buena —un montaje del director
    dura más que el minutaje nominal—, pero llega en cero hasta que el reproductor la
    conoce. En ese hueco vale la de la ficha: sin denominador no hay barra que dibujar.
    """
    if completed:return 1.0
    total=duration or runtime_seconds(data)*1000
    if not total or position<MINIMUM_PROGRESS_MS:return 0.0
    fraction=position/total
    # Los créditos no deben dejar para siempre un episodio al 99 %.
    if fraction>=COMPLETED_FRACTION:return 1.0
    return round(min(1.0,fraction),3)


class Library:
    def __init__(self, store): self.store=store

    def preferences(self,video_id):
        """Identidad estable y ajustes del último visionado; no contiene URLs privadas."""
        with self.store.connect() as conn:
            row=conn.execute('SELECT preferences FROM playback_progress WHERE video_id=%s',(video_id,)).fetchone()
        return row[0] if row else {}

    def favorites(self):
        with self.store.connect() as conn:
            rows=conn.execute("SELECT c.data FROM library l JOIN content c ON c.id=l.content_id WHERE l.favorite ORDER BY l.updated_at DESC").fetchall()
        return [row[0] for row in rows]

    def favorite(self,content_id,enabled):
        with self.store.connect() as conn:
            if not conn.execute("SELECT 1 FROM content WHERE id=%s",(content_id,)).fetchone():
                raise ProviderError("not_found","Abre la ficha antes de guardarla.",404)
            conn.execute("""INSERT INTO library(content_id,favorite) VALUES (%s,%s)
                ON CONFLICT(content_id) DO UPDATE SET favorite=EXCLUDED.favorite,updated_at=now()""",(content_id,enabled))
        return {"favorite":enabled}

    def continuing(self):
        with self.store.connect() as conn:
            rows=conn.execute("""SELECT c.data,p.video_id,p.position_ms,p.duration_ms,p.updated_at,p.completed FROM
                (SELECT DISTINCT ON(content_id) * FROM playback_progress
                 WHERE position_ms>0 ORDER BY content_id,updated_at DESC) p
                JOIN content c ON c.id=p.content_id WHERE c.media_type<>'tv'""").fetchall()
        return [{**row[0],"video_id":row[1],"position_ms":row[2],"duration_ms":row[3],
                 "progress":watched_fraction(row[0],row[2],row[3])}
                for row in sorted(rows,key=lambda r:r[4],reverse=True)
                if 0<watched_fraction(row[0],row[2],row[3],row[5])<1]

    def progress_map(self):
        """Cuánto se lleva visto de cada ficha, para marcarlo sobre las carátulas.

        Solo cuenta lo que se empezó de verdad: al pulsar Reproducir se crea la fila del
        video aunque no se vea un segundo, y sin este filtro una serie entera aparecería
        vista por haberla abierto.
        """
        with self.store.connect() as conn:
            rows=conn.execute("""SELECT DISTINCT ON(p.content_id) p.content_id,p.position_ms,p.duration_ms,p.completed,c.data
                FROM playback_progress p JOIN content c ON c.id=p.content_id
                WHERE p.position_ms>0 AND c.media_type<>'tv'
                ORDER BY p.content_id,p.updated_at DESC""").fetchall()
        seen={content_id:watched_fraction(data,position,duration,completed)
              for content_id,position,duration,completed,data in rows}
        return {content_id:value for content_id,value in seen.items() if value>0}

    def detail_progress(self,content_id):
        """Estado por película o episodio para construir una ficha inequívoca."""
        with self.store.connect() as conn:
            rows=conn.execute("""SELECT p.video_id,p.position_ms,p.duration_ms,p.completed,p.updated_at,c.data,p.preferences
                FROM playback_progress p JOIN content c ON c.id=p.content_id
                WHERE p.content_id=%s ORDER BY p.updated_at DESC""",(content_id,)).fetchall()
        videos={}
        for video_id,position,duration,completed,updated,data,preferences in rows:
            fraction=watched_fraction(data,position,duration,completed)
            videos[video_id]={"position_ms":position,"duration_ms":duration,
                              "progress":fraction,"completed":fraction>=1,
                              "updated_at":updated.isoformat(),"preferences":preferences}
        return {"videos":videos}

    def position(self,video_id):
        """Punto guardado de este video, sin abrir sesión: sirve para adelantar trabajo."""
        with self.store.connect() as conn:
            row=conn.execute("""SELECT p.position_ms,p.duration_ms,p.completed,c.data
                FROM playback_progress p JOIN content c ON c.id=p.content_id WHERE p.video_id=%s""",(video_id,)).fetchone()
        return 0 if not row or watched_fraction(row[3],row[0],row[1],row[2])>=1 else row[0]

    def session(self,content_id,video_id,actor):
        session_id=secrets.token_urlsafe(24)
        with self.store.connect() as conn:
            # Bloquear la ficha serializa sesiones simultáneas del mismo contenido.
            if not conn.execute("SELECT 1 FROM content WHERE id=%s FOR UPDATE",(content_id,)).fetchone():
                raise ProviderError("not_found","Abre primero la ficha del título.",404)
            row=conn.execute("""SELECT p.position_ms,p.duration_ms,p.completed,c.data,p.preferences
                FROM playback_progress p JOIN content c ON c.id=p.content_id WHERE p.video_id=%s""",(video_id,)).fetchone()
            position=0 if not row or watched_fraction(row[3],row[0],row[1],row[2])>=1 else row[0]
            duration=row[1] if row else 0
            conn.execute("INSERT INTO playback_session(id,video_id,content_id,actor) VALUES (%s,%s,%s,%s)",(session_id,video_id,content_id,actor))
            conn.execute("""INSERT INTO playback_progress(video_id,content_id,position_ms,duration_ms,session_id,sequence)
                VALUES (%s,%s,%s,%s,%s,-1) ON CONFLICT(video_id) DO UPDATE SET session_id=EXCLUDED.session_id,sequence=-1""",
                (video_id,content_id,position,duration,session_id))
        return {"session_id":session_id,"position_ms":position,"preferences":row[4] if row else {}}

    def progress(self,session_id,sequence,position,duration,completed,actor,watched_ms=None,preferences=None):
        if duration and position>duration+10000:
            raise ProviderError("invalid_progress","La posición excede la duración.",422)
        completed=bool(completed or (duration and position/duration>=COMPLETED_FRACTION))
        with self.store.connect() as conn:
            saved=None
            if preferences is not None:
                # La TV no puede asociar al historial una fuente de otra película ni
                # reescribir preferencias desde una sesión vieja o desde otra TV.
                source=conn.execute("""SELECT m.source_fingerprint FROM media_source m
                    JOIN playback_session s ON s.video_id=m.video_id AND s.content_id=m.content_id
                    JOIN playback_progress p ON p.video_id=s.video_id AND p.session_id=s.id
                    WHERE m.id=%s AND m.expires_at>now() AND s.id=%s AND s.actor=%s
                    AND s.expires_at>now() AND p.sequence<%s AND m.provider<>'subtitulos'""",
                    (preferences['source_id'],session_id,actor,sequence)).fetchone()
                if not source:return {'accepted':False}
                saved=json.dumps({key:value for key,value in preferences.items() if key!='source_id'} |
                                 {'source_fingerprint':source[0]})
            if watched_ms is not None and watched_ms<MINIMUM_PROGRESS_MS:
                # La sesión sigue ordenada, pero una apertura accidental no pisa ni crea
                # historial visible. Esto también protege "Ver desde el inicio".
                row=conn.execute("""UPDATE playback_progress p SET sequence=%s,preferences=COALESCE(%s::jsonb,p.preferences)
                    FROM playback_session s WHERE s.id=%s AND s.actor=%s AND s.expires_at>now()
                    AND p.video_id=s.video_id AND p.session_id=s.id AND p.sequence<%s RETURNING p.video_id""",
                    (sequence,saved,session_id,actor,sequence)).fetchone()
            else:
                row=conn.execute("""UPDATE playback_progress p SET position_ms=%s,duration_ms=%s,completed=%s,sequence=%s,
                    preferences=COALESCE(%s::jsonb,p.preferences),updated_at=now()
                    FROM playback_session s WHERE s.id=%s AND s.actor=%s AND s.expires_at>now()
                    AND p.video_id=s.video_id AND p.session_id=s.id AND p.sequence<%s RETURNING p.video_id""",
                    (position,duration,completed,sequence,saved,session_id,actor,sequence)).fetchone()
        return {"accepted":bool(row)}
