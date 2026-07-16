import { CONFIG } from '../config/api';
import { getHeaders } from './api';
import type {
  CreateDebateRequest,
  DebateState,
  AddSpeakerRequest,
  SpeakerConfig,
} from '../types/debate';

const BASE = () => `${CONFIG.baseUrl}/api/v1/debate`;

export async function createDebate(req: CreateDebateRequest, apiKey: string) {
  const resp = await fetch(`${BASE()}/create`, {
    method: 'POST',
    headers: getHeaders(apiKey),
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to create debate');
  return resp.json();
}

export async function generateDebateIdea(category: string, apiKey: string): Promise<{ topic: string; teaser: string; category: string }> {
  const resp = await fetch(`${BASE()}/idea`, {
    method: 'POST',
    headers: getHeaders(apiKey),
    body: JSON.stringify({ category }),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to generate debate idea');
  return resp.json();
}

export async function startDebate(sessionId: string, apiKey: string) {
  const resp = await fetch(`${BASE()}/${sessionId}/start`, {
    method: 'POST',
    headers: getHeaders(apiKey),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to start debate');
  return resp.json();
}

export async function stopDebate(sessionId: string, apiKey: string) {
  const resp = await fetch(`${BASE()}/${sessionId}/stop`, {
    method: 'POST',
    headers: getHeaders(apiKey),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to stop debate');
  return resp.json();
}

export async function getDebate(sessionId: string): Promise<DebateState> {
  const resp = await fetch(`${BASE()}/${sessionId}`);
  if (!resp.ok) throw new Error('Failed to get debate');
  return resp.json();
}

export async function addSpeaker(sessionId: string, req: AddSpeakerRequest, apiKey: string) {
  const resp = await fetch(`${BASE()}/${sessionId}/speaker`, {
    method: 'POST',
    headers: getHeaders(apiKey),
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to add speaker');
  return resp.json();
}

export async function removeSpeaker(sessionId: string, speakerId: string, apiKey: string) {
  const resp = await fetch(`${BASE()}/${sessionId}/speaker/${speakerId}`, {
    method: 'DELETE',
    headers: getHeaders(apiKey),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to remove speaker');
  return resp.json();
}

export async function updateSpeaker(sessionId: string, speakerId: string, req: AddSpeakerRequest, apiKey: string) {
  const resp = await fetch(`${BASE()}/${sessionId}/speaker/${speakerId}`, {
    method: 'PUT',
    headers: getHeaders(apiKey),
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error((await resp.json()).detail || 'Failed to update speaker');
  return resp.json();
}

export function streamDebate(sessionId: string, onEvent: (event: string, data: any) => void): AbortController {
  const controller = new AbortController();
  const es = new EventSource(`${BASE()}/${sessionId}/stream`);

  es.addEventListener('status', (e) => onEvent('status', JSON.parse(e.data)));
  es.addEventListener('turn', (e) => onEvent('turn', JSON.parse(e.data)));
  es.addEventListener('message', (e) => onEvent('message', JSON.parse(e.data)));
  es.addEventListener('progress', (e) => onEvent('progress', JSON.parse(e.data)));
  es.addEventListener('error', (e) => onEvent('error', JSON.parse((e as MessageEvent).data)));

  es.onerror = () => {
    es.close();
    onEvent('status', { status: 'disconnected' });
  };

  controller.signal.addEventListener('abort', () => es.close());
  return controller;
}
