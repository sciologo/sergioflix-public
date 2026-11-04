"""Alineación de voz con FFsubsync y validación independiente de la deriva.

Se ejecuta en un proceso acotado: el cálculo no bloquea la API ni la reproducción.
Los segundos no examinados son desconocidos (0,5), nunca silencio confirmado (0).
"""
import json
import logging
import sys
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pysubs2
from ffsubsync.aligners import FFTAligner, MaxScoreAligner
from ffsubsync.speech_transformers import VideoSpeechTransformer, make_subtitle_speech_pipeline

RATE = 100
WINDOW = 20
MAX_OFFSET = 120
MIN_CORRELATION = 0.30
MAX_RESIDUAL = 1.5


def sample_starts(duration):
    """Cuatro fragmentos cortos: dos para ajustar y otros dos para comprobar."""
    if not np.isfinite(duration) or duration < 360 or duration > 43200:
        raise ValueError("reference_too_short")
    return [int(min(duration - WINDOW - 15, max(30, duration * fraction)))
            for fraction in (0.10, 0.20, 0.75, 0.85)]


def extract_voice(job):
    starts = sample_starts(job["duration"])
    cache = Path(job['voice_cache']) if job.get('voice_cache') else None
    if cache and cache.exists():
        try:
            with np.load(cache, allow_pickle=False) as saved:
                voice = saved['voice']
            if len(voice) >= int(job['duration'] * RATE):
                return voice, [(start, voice[start * RATE:(start + WINDOW) * RATE]) for start in starts]
        except (OSError, ValueError, KeyError):
            pass

    def remember(voice, samples):
        if cache:
            temporary = cache.with_suffix('.tmp')
            with temporary.open('wb') as output:
                np.savez_compressed(output, voice=voice)
            temporary.replace(cache)
        return voice, samples

    def extract_sample(start):
        # Incluso con HLS completo, solo se leen cuatro fragmentos. FFsubsync
        # recibe WAV locales: nunca inspecciona ni descarga la película completa.
        command = [str(Path(job['ffmpeg_path']) / ('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')),
                   '-nostdin','-v','error','-ss',str(start),'-rw_timeout','2000000','-i',job['reference'],
                   '-t',str(WINDOW),'-map',job.get('audio_stream') or '0:a:0',
                   '-vn','-sn','-ac','1','-ar','48000','-f','wav','pipe:1']
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2.5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        if result.returncode:
            raise ValueError('audio_decode_failed')
        if len(result.stdout) < 48_000 * 2 * (WINDOW - 2):
            raise ValueError('incomplete_audio_sample')
        with tempfile.TemporaryDirectory(prefix='subtitle-voice-') as folder:
            path = Path(folder) / 'sample.wav'
            path.write_bytes(result.stdout)
            detector = VideoSpeechTransformer(vad='webrtc', sample_rate=RATE, frame_rate=48000,
                non_speech_label=0, ffmpeg_path=job['ffmpeg_path'])
            try:
                detector.fit(str(path))
            except Exception:
                raise ValueError('voice_detection_failed') from None
            return detector.transform()

    # Dos lecturas como máximo; las descargas comparten el proxy protegido del PC.
    with ThreadPoolExecutor(max_workers=2) as pool:
        windows = list(pool.map(extract_sample, starts))
    reference = np.full(int(job["duration"] * RATE) + 1, 0.5)
    samples = []
    for start, voice in zip(starts, windows):
        if len(voice) < (WINDOW - 2) * RATE:
            raise ValueError("incomplete_audio_sample")
        voice = voice[:WINDOW * RATE]
        reference[start * RATE:start * RATE + len(voice)] = voice
        samples.append((start, voice))
    return remember(reference, samples)


def speech_signal(subtitles, scale, offset, length):
    """Presencia de texto en la misma escala de voz, sin guardar los diálogos."""
    signal = np.zeros(length)
    for cue in subtitles:
        start = max(0, round((cue.start / 1000 * scale + offset) * RATE))
        end = max(0, round((cue.end / 1000 * scale + offset) * RATE))
        if end > start:
            signal[start:min(length, end, start + 10 * RATE)] = 1
    return signal


def window_match(signal, start, voice, radius=12):
    """Correlación normalizada: una ventana hablada no gana solo por tener más texto."""
    n, margin = len(voice), int(radius * RATE)
    if n < (WINDOW - 2) * RATE or not 0.08 < np.mean(voice) < 0.92:
        raise ValueError("insufficient_dialogue")
    low = start * RATE - margin
    candidate = signal[low: start * RATE + n + margin]
    if low < 0 or len(candidate) != n + 2 * margin:
        raise ValueError("incomplete_subtitle_sample")
    centered = voice - np.mean(voice)
    size = 1 << (len(candidate) + n - 2).bit_length()
    convolution = np.fft.irfft(
        np.fft.rfft(candidate, size) * np.fft.rfft(centered[::-1], size), size
    )[n - 1:len(candidate)]
    cumulative = np.r_[0, np.cumsum(candidate)]
    sums = cumulative[n:] - cumulative[:-n]
    variance = np.maximum(0, sums - sums * sums / n)
    denominator = np.sqrt(variance * np.sum(centered ** 2))
    scores = np.divide(convolution, denominator, out=np.full_like(convolution, -1), where=denominator > 0)
    best = int(np.argmax(scores))
    # Exige un pico distinguible de otras frases, además de una puntuación positiva.
    other = scores.copy()
    other[max(0, best - RATE):best + RATE + 1] = -1
    confidence = float(scores[best])
    if confidence < MIN_CORRELATION or confidence - float(np.max(other)) < 0.04:
        raise ValueError("ambiguous_dialogue_match")
    return (best - margin) / RATE, confidence


def align(subtitles, input_path, reference, samples):
    """Reutiliza el ranking de escalas de FFsubsync; valida lo que cambia antes de escribir."""
    maker = make_subtitle_speech_pipeline(scale_factor=None, encoding="utf-8")
    ratios = [1, 24 / 23.976, 25 / 23.976, 25 / 24]
    ratios += [1 / ratio for ratio in ratios[1:]]
    pipelines = [maker(ratio).fit(str(input_path)) for ratio in ratios]
    # Las relaciones habituales bastan para el ajuste inicial; la deriva se
    # estima después. Evita una búsqueda continua costosa durante la reproducción.
    (_, offset_samples), pipeline = MaxScoreAligner(
        FFTAligner, str(input_path), RATE, MAX_OFFSET
    ).fit_transform(reference, pipelines)
    scale = float(pipeline.named_steps["scale"].scale_factor)
    offset = float(offset_samples / RATE)
    length = max(len(reference), int(max(c.end for c in subtitles) / 1000 * 1.15 * RATE)) + 20 * RATE
    signal = speech_signal(subtitles, scale, offset, length)

    # Una escala aproximada puede alinear la mitad y fallar en ambos extremos.
    # Calcula con dos ventanas alejadas y comprueba con otras dos independientes.
    anchors, qualities = [], []
    for start, voice in samples[::2]:
        try:
            residual, quality = window_match(signal, start, voice)
        except ValueError:
            continue
        anchors.append((start + len(voice) / RATE / 2, residual))
        qualities.append(quality)
    if len(anchors) < 2 or anchors[-1][0] - anchors[0][0] < len(reference) / RATE * 0.4:
        raise ValueError("alignment_inconclusive")
    slope, intercept = np.polyfit(*np.array(anchors).T, 1)
    if abs(slope) > 0.015 or max(abs(y - (slope * x + intercept)) for x, y in anchors) > MAX_RESIDUAL:
        raise ValueError("inconsistent_drift")
    scale, offset = scale / (1 + slope), (offset - intercept) / (1 + slope)
    if not 0.9 <= scale <= 1.1 or abs(offset) > MAX_OFFSET:
        raise ValueError("implausible_correction")
    adjusted = speech_signal(subtitles, scale, offset, length)
    residuals, validation_starts = [], []
    for start, voice in samples[1::2]:
        try:
            residual, quality = window_match(adjusted, start, voice, radius=3)
        except ValueError:
            continue
        if abs(residual) > MAX_RESIDUAL:
            raise ValueError("validation_failed")
        residuals.append(residual)
        validation_starts.append(start)
        qualities.append(quality)
    if (len(residuals) < 2 or validation_starts[-1] - validation_starts[0] < len(reference) / RATE * 0.4
            or np.median(qualities) < 0.30):
        raise ValueError("weak_alignment")
    return {
        "accepted": True, "scale": float(round(scale, 8)), "offset_seconds": float(round(offset, 3)),
        "quality": round(float(np.median(qualities)), 3),
        "max_residual_seconds": round(max(abs(value) for value in residuals), 3),
        "samples": len(samples),
    }


def run(job):
    original = pysubs2.load(job["input"], encoding="utf-8")
    if not original:
        raise ValueError("empty_subtitle")
    end = max(cue.end for cue in original) / 1000
    if end < job['duration'] * .78 or end > max(job["duration"] * 1.12, job["duration"] + 120):
        raise ValueError("subtitle_duration_mismatch")
    reference, samples = extract_voice(job)
    result = align(original, Path(job["input"]), reference, samples)
    # Ajusta ambos límites de cada entrada y elimina únicamente lo anterior al inicio.
    for cue in original:
        cue.start = round(cue.start * result["scale"] + result["offset_seconds"] * 1000)
        cue.end = round(cue.end * result["scale"] + result["offset_seconds"] * 1000)
    original.events = [cue for cue in original if cue.end > 0]
    for cue in original:
        cue.start = max(0, cue.start)
    original.save(job["output"], encoding="utf-8")
    return result


if __name__ == "__main__":
    # Los logs de las dependencias pueden contener direcciones del input: no salen del proceso.
    logging.disable(logging.CRITICAL)
    try:
        job = json.loads(sys.stdin.read())
        print(json.dumps(run(job)))
    except Exception as error:
        reasons = {
            "reference_too_short", "incomplete_audio_sample", "insufficient_dialogue",
            "incomplete_subtitle_sample", "ambiguous_dialogue_match", "inconsistent_drift",
            "implausible_correction", "validation_failed", "weak_alignment", "empty_subtitle",
            "subtitle_duration_mismatch",
            "audio_decode_failed", "voice_detection_failed", "alignment_inconclusive",
        }
        reason = 'audio_read_timeout' if isinstance(error, subprocess.TimeoutExpired) else (
            str(error) if isinstance(error, ValueError) and str(error) in reasons else "audio_analysis_failed")
        # La clase es segura para auditoría; el mensaje podría contener una URL.
        print(json.dumps({"accepted": False, "reason": reason, "error_code": type(error).__name__}))
