use anyhow::{Context, Result, bail};

const SAMPLE_RATE: usize = 24_000;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cue {
    pub kind: String,
    pub value: String,
    pub char_pos: usize,
}

pub fn extract(text: &str) -> (String, Vec<Cue>) {
    let mut clean = String::new();
    let mut cues = Vec::new();
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'['
            && let Some(end_rel) = text[i + 1..].find(']')
        {
            let end = i + 1 + end_rel;
            let tag = &text[i + 1..end];
            if let Some((kind, value)) = tag.split_once(':') {
                let kind = kind.trim().to_ascii_lowercase();
                if matches!(kind.as_str(), "sfx" | "ambience" | "silence") {
                    cues.push(Cue {
                        kind,
                        value: value.trim().to_ascii_lowercase(),
                        char_pos: clean.chars().count(),
                    });
                    i = end + 1;
                    continue;
                }
            }
        }
        let ch = text[i..].chars().next().expect("valid utf-8");
        clean.push(ch);
        i += ch.len_utf8();
    }
    (clean.split_whitespace().collect::<Vec<_>>().join(" "), cues)
}

pub fn mix_wav(wav: &[u8], cues: &[Cue], text_chars: usize) -> Result<Vec<u8>> {
    let mut voice = decode_pcm16_mono(wav)?;
    let original_len = voice.len();
    for cue in cues.iter().filter(|cue| cue.kind == "silence") {
        let ms = parse_millis(&cue.value).unwrap_or(500).min(10_000);
        let at = cue_sample(cue.char_pos, text_chars, voice.len());
        voice.splice(at..at, std::iter::repeat_n(0.0, ms * SAMPLE_RATE / 1000));
    }
    let mut mixed = voice;
    for cue in cues.iter().filter(|cue| cue.kind != "silence") {
        let at = cue_sample(cue.char_pos, text_chars, original_len).min(mixed.len());
        let effect = synth_effect(&cue.value);
        if cue.kind == "ambience" {
            overlay_looped(&mut mixed, &effect, at, 0.18, true);
        } else {
            overlay_once(&mut mixed, &effect, at, 0.45, true);
        }
    }
    encode_pcm16_mono(&mixed)
}

fn cue_sample(char_pos: usize, text_chars: usize, samples: usize) -> usize {
    samples
        .saturating_mul(char_pos)
        .checked_div(text_chars)
        .unwrap_or(0)
}

fn parse_millis(value: &str) -> Option<usize> {
    value.trim().trim_end_matches("ms").parse().ok()
}

fn synth_effect(name: &str) -> Vec<f32> {
    match name {
        "station" | "station_ambience" | "bahnhof" => station(),
        "train" | "train_arriving" | "zug_ankunft" => train_arriving(),
        "rain" | "rain_heavy" | "regen" => filtered_noise(8.0, 0x2026, 0.12),
        "thunder" | "thunder_close" | "gewitter" => thunder(),
        _ => Vec::new(),
    }
}

fn station() -> Vec<f32> {
    let mut out = filtered_noise(10.0, 0x5107, 0.07);
    for (i, sample) in out.iter_mut().enumerate() {
        let t = i as f32 / SAMPLE_RATE as f32;
        *sample += (t * 110.0 * std::f32::consts::TAU).sin() * 0.018;
    }
    out
}

fn train_arriving() -> Vec<f32> {
    let len = 6 * SAMPLE_RATE;
    let mut out = vec![0.0; len];
    let mut phase = 0.0_f32;
    for (i, sample) in out.iter_mut().enumerate() {
        let progress = i as f32 / len as f32;
        let freq = 65.0 + progress * 55.0;
        phase += std::f32::consts::TAU * freq / SAMPLE_RATE as f32;
        *sample = phase.sin() * (0.03 + progress * 0.16);
    }
    let brakes = filtered_noise(2.2, 0xB4A9, 0.16);
    let start = len - brakes.len();
    for (dst, src) in out[start..].iter_mut().zip(brakes) {
        *dst += src;
    }
    out
}

fn thunder() -> Vec<f32> {
    let len = 3 * SAMPLE_RATE;
    let mut out = filtered_noise(3.0, 0x7A11, 0.10);
    for (i, sample) in out.iter_mut().enumerate() {
        let t = i as f32 / SAMPLE_RATE as f32;
        let fade = 1.0 - i as f32 / len as f32;
        *sample = (*sample + (t * 48.0 * std::f32::consts::TAU).sin() * 0.28) * fade;
    }
    out
}

fn filtered_noise(seconds: f32, mut state: u32, gain: f32) -> Vec<f32> {
    let len = (seconds * SAMPLE_RATE as f32) as usize;
    let mut out = vec![0.0; len];
    let mut smooth = 0.0_f32;
    for sample in &mut out {
        state = state.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
        let raw = ((state >> 8) as f32 / 16_777_215.0) * 2.0 - 1.0;
        smooth = smooth * 0.92 + raw * 0.08;
        *sample = smooth * gain;
    }
    out
}

fn overlay_once(dst: &mut Vec<f32>, src: &[f32], at: usize, gain: f32, duck: bool) {
    if src.is_empty() {
        return;
    }
    if dst.len() < at + src.len() {
        dst.resize(at + src.len(), 0.0);
    }
    for (index, sample) in src.iter().enumerate() {
        let pos = at + index;
        if duck && dst[pos].abs() > 0.01 {
            dst[pos] *= 0.72;
        }
        dst[pos] = (dst[pos] + sample * gain).clamp(-0.98, 0.98);
    }
}

fn overlay_looped(dst: &mut [f32], src: &[f32], at: usize, gain: f32, duck: bool) {
    if src.is_empty() || at >= dst.len() {
        return;
    }
    for pos in at..dst.len() {
        if duck && dst[pos].abs() > 0.01 {
            dst[pos] *= 0.78;
        }
        dst[pos] = (dst[pos] + src[(pos - at) % src.len()] * gain).clamp(-0.98, 0.98);
    }
}

fn decode_pcm16_mono(wav: &[u8]) -> Result<Vec<f32>> {
    if wav.len() < 44 || &wav[0..4] != b"RIFF" || &wav[8..12] != b"WAVE" {
        bail!("invalid WAV");
    }
    let channels = u16::from_le_bytes([wav[22], wav[23]]);
    let rate = u32::from_le_bytes([wav[24], wav[25], wav[26], wav[27]]);
    let bits = u16::from_le_bytes([wav[34], wav[35]]);
    if channels != 1 || rate != SAMPLE_RATE as u32 || bits != 16 {
        bail!("expected mono 24kHz PCM16 WAV");
    }
    let data_pos = wav
        .windows(4)
        .position(|w| w == b"data")
        .context("WAV data chunk missing")?;
    let size = u32::from_le_bytes(wav[data_pos + 4..data_pos + 8].try_into()?) as usize;
    let start = data_pos + 8;
    let end = (start + size).min(wav.len());
    Ok(wav[start..end]
        .chunks_exact(2)
        .map(|b| i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0)
        .collect())
}

fn encode_pcm16_mono(samples: &[f32]) -> Result<Vec<u8>> {
    let data_len = samples.len().checked_mul(2).context("WAV too large")?;
    let mut out = Vec::with_capacity(44 + data_len);
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(36_u32 + data_len as u32).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16_u32.to_le_bytes());
    out.extend_from_slice(&1_u16.to_le_bytes());
    out.extend_from_slice(&1_u16.to_le_bytes());
    out.extend_from_slice(&(SAMPLE_RATE as u32).to_le_bytes());
    out.extend_from_slice(&((SAMPLE_RATE * 2) as u32).to_le_bytes());
    out.extend_from_slice(&2_u16.to_le_bytes());
    out.extend_from_slice(&16_u16.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&(data_len as u32).to_le_bytes());
    for sample in samples {
        out.extend_from_slice(&((sample.clamp(-1.0, 1.0) * 32767.0) as i16).to_le_bytes());
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_audio_cues_without_speaking_them() {
        let (clean, cues) = extract(
            "[ambience:station] Der Zug kam an. [sfx:train_arriving] [silence:700ms] Mara sprach.",
        );
        assert_eq!(clean, "Der Zug kam an. Mara sprach.");
        assert_eq!(cues.len(), 3);
    }
}
