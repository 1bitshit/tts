import { CONFIG } from '../config/api';
import { getHeaders } from './api';

const BASE = () => `${CONFIG.baseUrl}/api/v1/story`;

export interface StoryCharacter {
  id: string;
  name: string;
  role: string;
  personality: string;
  voice_description: string;
  model_name: string;
  language: string;
}

export interface StoryMessage {
  speaker_id: string;
  speaker_name: string;
  text: string;
  audio_base64: string | null;
  timestamp: string;
  scene: number;
}

export interface StoryState {
  session_id: string;
  title: string;
  premise: string;
  genre: string;
  model_name: string;
  characters: StoryCharacter[];
  messages: StoryMessage[];
  status: string;
  current_scene: number;
  volume?: number;
  max_scenes: number;
  progress?: { percent: number; label: string };
}

async function json<T>(response: Response): Promise<T> {
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Story request failed');
  return data;
}

export async function createStory(payload: object, apiKey: string): Promise<StoryState> {
  return json(await fetch(`${BASE()}/create`, { method: 'POST', headers: getHeaders(apiKey), body: JSON.stringify(payload) }));
}

export async function listStories(apiKey: string) {
  return json<Array<{ session_id: string; title: string; status: string; message_count: number; updated_at: string }>>(
    await fetch(BASE(), { headers: getHeaders(apiKey, '') }),
  );
}

export async function getStory(id: string, apiKey: string): Promise<StoryState> {
  return json(await fetch(`${BASE()}/${id}`, { headers: getHeaders(apiKey, '') }));
}

export async function startStory(id: string, apiKey: string) {
  return json(await fetch(`${BASE()}/${id}/start`, { method: 'POST', headers: getHeaders(apiKey, '') }));
}

export async function stopStory(id: string, apiKey: string) {
  return json(await fetch(`${BASE()}/${id}/stop`, { method: 'POST', headers: getHeaders(apiKey, '') }));
}

export function streamStory(id: string, onEvent: (event: string, data: any) => void): AbortController {
  const controller = new AbortController();
  const stream = new EventSource(`${BASE()}/${id}/stream`);
  for (const event of ['status', 'turn', 'text', 'message', 'progress', 'error']) {
    stream.addEventListener(event, (message) => onEvent(event, JSON.parse((message as MessageEvent).data)));
  }
  stream.onerror = () => { stream.close(); onEvent('status', { status: 'disconnected' }); };
  controller.signal.addEventListener('abort', () => stream.close());
  return controller;
}
