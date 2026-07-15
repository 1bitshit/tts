export interface SpeakerConfig {
  id: string;
  name: string;
  personality: string;
  model_name: string;
  voice_description: string;
  language: string;
}

export interface DebateMessage {
  speaker_id: string;
  speaker_name: string;
  text: string;
  audio_base64: string | null;
  timestamp: string;
  round: number;
}

export interface DebateState {
  session_id: string;
  topic: string;
  speakers: SpeakerConfig[];
  messages: DebateMessage[];
  status: 'idle' | 'running' | 'paused' | 'stopped' | 'finished';
  current_round: number;
  current_speaker_index: number;
  max_rounds: number;
  auto_advance: boolean;
}

export interface CreateDebateRequest {
  topic: string;
  speakers: SpeakerConfig[];
  max_rounds: number;
  auto_advance: boolean;
  delay_between_speakers: number;
}

export interface AddSpeakerRequest {
  name: string;
  personality: string;
  model_name: string;
  voice_description: string;
  language: string;
}

export type SSEEvent = {
  event: 'status' | 'turn' | 'message' | 'error';
  data: any;
};
