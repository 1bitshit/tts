use crate::request::SpeechRequest;
use async_trait::async_trait;
use bytes::Bytes;
use futures_core::Stream;
use std::pin::Pin;

pub type AudioStream = Pin<Box<dyn Stream<Item = anyhow::Result<Bytes>> + Send>>;

#[derive(Debug, Clone)]
pub struct BackendHealth {
    pub ready: bool,
    pub name: &'static str,
    pub detail: Option<String>,
}

#[async_trait]
pub trait TtsBackend: Send + Sync + 'static {
    async fn health(&self) -> BackendHealth;
    async fn speakers(&self) -> anyhow::Result<serde_json::Value>;
    async fn synthesize(&self, request: &SpeechRequest) -> anyhow::Result<Bytes>;
    async fn stream(&self, request: &SpeechRequest) -> anyhow::Result<AudioStream>;
}
