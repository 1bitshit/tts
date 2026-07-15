import { CONFIG } from '../config/api';
import { getHeaders } from './api';

const BASE = () => `${CONFIG.baseUrl}/api/v1/archive`;

export async function listDebates(apiKey: string) {
  const resp = await fetch(`${BASE()}/debates`, { headers: getHeaders(apiKey) });
  if (!resp.ok) throw new Error('Failed to list archived debates');
  return resp.json();
}

export async function getDebate(sessionId: string) {
  const resp = await fetch(`${BASE()}/debates/${sessionId}`);
  if (!resp.ok) throw new Error('Failed to get archived debate');
  return resp.json();
}

export async function deleteDebate(sessionId: string, apiKey: string) {
  const resp = await fetch(`${BASE()}/debates/${sessionId}`, {
    method: 'DELETE',
    headers: getHeaders(apiKey),
  });
  if (!resp.ok) throw new Error('Failed to delete archived debate');
  return resp.json();
}

export async function listPrompts(apiKey: string) {
  const resp = await fetch(`${BASE()}/prompts`, { headers: getHeaders(apiKey) });
  if (!resp.ok) throw new Error('Failed to list archived prompts');
  return resp.json();
}

export async function listClips(apiKey: string) {
  const resp = await fetch(`${BASE()}/clips`, { headers: getHeaders(apiKey) });
  if (!resp.ok) throw new Error('Failed to list archived clips');
  return resp.json();
}

export function downloadZipUrl() {
  return `${BASE()}/download-zip`;
}
