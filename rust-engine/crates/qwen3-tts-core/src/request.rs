use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpeechRequest {
    #[serde(alias = "input")]
    pub text: String,
    #[serde(default = "default_speaker", alias = "voice")]
    pub speaker: String,
    #[serde(default = "default_language")]
    pub language: String,
    pub emotion: Option<String>,
    pub instruct: Option<String>,
    #[serde(default = "one")]
    pub rate: f32,
    #[serde(default = "one")]
    pub volume: f32,
    #[serde(default = "default_temperature")]
    pub temperature: f32,
    #[serde(default = "default_top_k")]
    pub top_k: u32,
    #[serde(default = "one")]
    pub top_p: f32,
    #[serde(default = "default_rep_penalty")]
    pub rep_penalty: f32,
    pub seed: Option<u32>,
    #[serde(default = "default_chunk_frames")]
    pub chunk_frames: u16,
}

fn default_speaker() -> String {
    "vivian".into()
}
fn default_language() -> String {
    "German".into()
}
fn one() -> f32 {
    1.0
}
fn default_temperature() -> f32 {
    1.1
}
fn default_top_k() -> u32 {
    50
}
fn default_rep_penalty() -> f32 {
    1.08
}
fn default_chunk_frames() -> u16 {
    10
}

impl SpeechRequest {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.text.is_empty() || self.text.len() > 8192 {
            return Err("text must contain 1..8192 bytes");
        }
        if !(0.0..=2.0).contains(&self.temperature) {
            return Err("temperature must be 0..2");
        }
        if !(0.0..=1.0).contains(&self.top_p) {
            return Err("top_p must be 0..1");
        }
        if !(0.5..=2.0).contains(&self.rep_penalty) {
            return Err("rep_penalty must be 0.5..2");
        }
        if !(2..=250).contains(&self.chunk_frames) {
            return Err("chunk_frames must be 2..250");
        }
        Ok(())
    }
}
