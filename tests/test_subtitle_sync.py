"""Regresiones de sincronía: adelanto, atraso, deriva, audio no examinado y cortes."""
import numpy as np
import pysubs2
import pytest

from app.subtitle_sync import RATE, WINDOW, align, sample_starts, window_match, extract_voice


def example(tmp_path, scale=1, offset=0):
    rng = np.random.default_rng(84)
    voice = np.zeros(1200 * RATE)
    subs = pysubs2.SSAFile()
    position = 2.0
    while position < 1190:
        duration = rng.uniform(0.8, 3.5)
        voice[round(position * RATE):round((position + duration) * RATE)] = 1
        subs.append(pysubs2.SSAEvent(start=round((position - offset) / scale * 1000),
                                   end=round((position + duration - offset) / scale * 1000), text="Diálogo"))
        position += duration + rng.uniform(0.4, 2.8)
    path = tmp_path / "original.srt"
    subs.save(str(path), encoding="utf-8")
    samples = [(start, voice[start * RATE:(start + WINDOW) * RATE]) for start in sample_starts(1200)]
    return voice, subs, path, samples


@pytest.mark.parametrize("scale,offset", [(1, 0), (1, 5), (1, -5), (24 / 25, -2)])
def test_alignment_recovers_constant_offset_and_progressive_drift(tmp_path, scale, offset):
    voice, subs, path, samples = example(tmp_path, scale, offset)
    result = align(subs, path, voice, samples)
    assert result["accepted"] and result["max_residual_seconds"] <= 0.15
    assert abs(result["scale"] - scale) < 0.0002
    assert abs(result["offset_seconds"] - offset) < 0.15


def test_unsampled_audio_is_unknown_and_does_not_reject_a_perfect_match(tmp_path):
    voice, subs, path, samples = example(tmp_path)
    reference = np.full_like(voice, 0.5)
    for start, sample in samples:
        reference[start * RATE:start * RATE + len(sample)] = sample
    result = align(subs, path, reference, samples)
    assert result["accepted"] and result["max_residual_seconds"] < 0.1


def test_a_scene_cut_that_breaks_the_global_mapping_is_rejected(tmp_path):
    voice, subs, path, samples = example(tmp_path)
    for cue in subs:
        if cue.start > 600000:
            cue.start += 12000
            cue.end += 12000
    subs.save(str(path), encoding="utf-8")
    with pytest.raises(ValueError):
        align(subs, path, voice, samples)


def test_silence_cannot_validate_subtitles():
    with pytest.raises(ValueError, match="insufficient_dialogue"):
        window_match(np.ones(100000), 30, np.zeros(6000))


def test_short_reference_does_not_pretend_to_check_beginning_middle_and_end():
    with pytest.raises(ValueError, match="reference_too_short"):
        sample_starts(120)


def test_candidates_share_voice_analysis_without_storing_dialogues(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls, reads = [], []
    class Detector:
        def __init__(self, **_): pass
        def fit(self, reference): calls.append(reference)
        def transform(self): return np.zeros(WINDOW * RATE)
    def read(command, **kwargs):
        reads.append(command)
        return SimpleNamespace(returncode=0,stdout=b'\0'*(48_000*2*WINDOW))
    monkeypatch.setattr('app.subtitle_sync.VideoSpeechTransformer', Detector)
    monkeypatch.setattr('app.subtitle_sync.subprocess.run', read)
    job = {'duration':1200, 'local':True, 'reference':'local-video', 'ffmpeg_path':'unused',
           'voice_cache':str(tmp_path / 'voice.npz')}
    first = extract_voice(job)
    second = extract_voice(job)
    assert len(calls) == len(reads) == 4
    assert all('-ss' in command and command[command.index('-t')+1] == str(WINDOW) for command in reads)
    assert len(first[1]) == len(second[1]) == 4
    assert np.count_nonzero(first[0] == .5) >= 1100*RATE
    with np.load(job['voice_cache'], allow_pickle=False) as cached:
        assert cached.files == ['voice']


def test_real_ffmpeg_and_vad_only_examine_short_samples_even_for_local_files(tmp_path):
    import wave
    from pathlib import Path
    from app.media_tools import executable
    from app.providers.stremio import ProviderError
    try:ffmpeg=executable('ffmpeg')
    except ProviderError:pytest.skip('FFmpeg no está disponible')
    reference=tmp_path/'reference.wav'
    with wave.open(str(reference),'wb') as sound:
        sound.setnchannels(1);sound.setsampwidth(2);sound.setframerate(16000)
        sound.writeframes(b'\0'*(400*16000*2))
    voice,samples=extract_voice({'duration':400,'local':True,'reference':str(reference),
        'ffmpeg_path':str(Path(ffmpeg).parent),'audio_stream':'0:a:0'})
    assert len(samples)==4 and all(len(sample)==WINDOW*RATE for _,sample in samples)
    assert np.count_nonzero(voice==.5)==len(voice)-4*WINDOW*RATE
    assert not np.any(voice[voice!=.5])
