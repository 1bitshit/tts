import React, { useEffect, useRef, useState } from 'react';
import { BookOpen, Loader, Play, Save, Square } from 'lucide-react';
import { Card } from '../../ui/Card';
import { Button } from '../../ui/Button';
import { useAppContext } from '../../../context/AppContext';
import { useToast } from '../../../context/ToastContext';
import * as storyApi from '../../../services/story';
import type { StoryMessage, StoryState } from '../../../services/story';

export function StoryTab() {
  const { apiKey } = useAppContext();
  const toast = useToast();
  const [title, setTitle] = useState('Die Uhr der verlorenen Erinnerungen');
  const [premise, setPremise] = useState('Eine Uhrmacherin entdeckt eine Taschenuhr, die fremde Erinnerungen speichert.');
  const [genre, setGenre] = useState('Mystery-Fantasy');
  const [model, setModel] = useState('Qwen/Qwen3-0.6B-GGUF');
  const [deliveryMode, setDeliveryMode] = useState<'live' | 'prerecorded'>('live');
  const [progress, setProgress] = useState({ percent: 0, label: 'Bereit' });
  const [story, setStory] = useState<StoryState | null>(null);
  const [saved, setSaved] = useState<Array<{ session_id: string; title: string; status: string; message_count: number; updated_at: string }>>([]);
  const [busy, setBusy] = useState(false);
  const streamRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const refreshSaved = async () => {
    try { setSaved(await storyApi.listStories(apiKey)); } catch { /* API key may not be set yet */ }
  };

  useEffect(() => { void refreshSaved(); }, [apiKey]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [story?.messages]);
  useEffect(() => () => streamRef.current?.abort(), []);

  const connectStream = (id: string) => {
    streamRef.current?.abort();
    streamRef.current = storyApi.streamStory(id, (event, data) => {
      if (event === 'status') setStory((current) => current ? { ...current, status: data.status } : current);
      if (event === 'message') {
        setStory((current) => current ? {
          ...current,
          status: 'running',
          current_scene: Math.max(current.current_scene, data.scene),
          messages: [...current.messages.filter((item) => !item.text.startsWith('[Schreibt')), data as StoryMessage],
        } : current);
        if (deliveryMode === 'live' && data.audio_base64) void new Audio(`data:audio/wav;base64,${data.audio_base64}`).play();
      }
      if (event === 'progress') setProgress({ percent: data.percent, label: data.label });
      if (event === 'turn') setStory((current) => current ? {
        ...current,
        messages: [...current.messages, {
          speaker_id: data.speaker_id, speaker_name: data.speaker_name,
          text: '[Schreibt weiter …]', audio_base64: null, timestamp: '', scene: data.scene,
        }],
      } : current);
      if (event === 'error') toast.showToast(data.message || 'Story-Fehler', 'error');
    });
  };

  const create = async () => {
    setBusy(true);
    try {
      const created = await storyApi.createStory({ title, premise, genre, model_name: model, max_scenes: 100, delivery_mode: deliveryMode }, apiKey);
      setStory(created);
      await refreshSaved();
      toast.showToast('Geschichte gespeichert und bereit', 'success');
    } catch (error) { toast.showToast((error as Error).message, 'error'); }
    finally { setBusy(false); }
  };

  const start = async () => {
    if (!story) return;
    connectStream(story.session_id);
    try { await storyApi.startStory(story.session_id, apiKey); }
    catch (error) { streamRef.current?.abort(); toast.showToast((error as Error).message, 'error'); }
  };

  const stop = async () => {
    if (!story) return;
    await storyApi.stopStory(story.session_id, apiKey);
    streamRef.current?.abort();
    setStory({ ...story, status: 'stopped' });
    await refreshSaved();
  };

  const resume = async (id: string) => {
    setBusy(true);
    try { setStory(await storyApi.getStory(id, apiKey)); }
    catch (error) { toast.showToast((error as Error).message, 'error'); }
    finally { setBusy(false); }
  };

  return <div className="space-y-lg">
    <Card title="📖 Fortlaufende Geschichte" icon={BookOpen}>
      <div className="grid md:grid-cols-2 gap-md">
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Titel" className="px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" />
        <input value={genre} onChange={(e) => setGenre(e.target.value)} placeholder="Genre" className="px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" />
      </div>
      <textarea value={premise} onChange={(e) => setPremise(e.target.value)} rows={3} className="w-full mt-md px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" />
      <select value={model} onChange={(e) => setModel(e.target.value)} className="w-full mt-md px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" aria-label="Story-Modell">
        <option value="Qwen/Qwen3-0.6B-GGUF">Qwen/Qwen3-0.6B-GGUF (schnell)</option>
        <option value="Qwen/Qwen3-1.7B-GGUF">Qwen/Qwen3-1.7B-GGUF (bessere Storys)</option>
      </select>
      <select value={deliveryMode} onChange={(e) => setDeliveryMode(e.target.value as 'live' | 'prerecorded')} className="w-full mt-md px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" aria-label="Story-Wiedergabemodus">
        <option value="live">Live-Streaming – Beiträge sofort anzeigen und abspielen</option>
        <option value="prerecorded">Vorproduziert – erzeugen und später abspielen</option>
      </select>
      {(busy || story?.status === 'running') && <div className="mt-md">
        <div className="flex justify-between text-xs text-text-secondary mb-xs"><span>{progress.label}</span><span>{progress.percent}%</span></div>
        <div className="h-2 rounded bg-bg-surface overflow-hidden"><div className="h-full bg-accent-cyan transition-all duration-300" style={{ width: `${progress.percent}%` }} /></div>
      </div>}
      <div className="flex gap-sm mt-md flex-wrap">
        {!story && <Button onClick={create} isLoading={busy} icon={Save}>Neue Geschichte</Button>}
        {story && story.status !== 'running' && <Button onClick={start} icon={Play}>Weiter erzählen</Button>}
        {story?.status === 'running' && <Button onClick={stop} variant="danger" icon={Square}>Stoppen & speichern</Button>}
        {story && <Button variant="secondary" onClick={() => setStory(null)}>Andere Geschichte</Button>}
      </div>
    </Card>

    {!story && <Card title="Gespeicherte Geschichten">
      <div className="space-y-sm">
        {saved.length === 0 && <p className="text-text-muted">Noch keine Geschichten gespeichert.</p>}
        {saved.map((item) => <button key={item.session_id} onClick={() => resume(item.session_id)} className="w-full p-md text-left rounded bg-bg-surface border border-border-subtle hover:border-accent-cyan">
          <div className="text-text-primary font-semibold">{item.title}</div>
          <div className="text-xs text-text-muted">{item.message_count} Beiträge · {item.status} · {new Date(item.updated_at).toLocaleString()}</div>
        </button>)}
      </div>
    </Card>}

    {story && <Card title={`${story.title} · Szene ${story.current_scene}`}>
      <div className="max-h-[620px] overflow-y-auto space-y-md">
        {story.messages.map((message, index) => <div key={`${message.timestamp}-${index}`} className="p-md rounded bg-bg-surface border-l-2 border-accent-cyan">
          <div className="flex items-center gap-sm text-xs text-accent-cyan mb-xs">
            <span>{message.speaker_name}</span><span className="text-text-muted">Szene {message.scene}</span>
            {message.audio_base64 && <button className="ml-auto" onClick={() => void new Audio(`data:audio/wav;base64,${message.audio_base64}`).play()}><Play size={13} /></button>}
          </div>
          <div className={`whitespace-pre-wrap text-sm ${message.text.startsWith('[Schreibt') ? 'text-text-muted animate-pulse' : 'text-text-primary'}`}>{message.text}</div>
        </div>)}
        {story.status === 'running' && story.messages.length === 0 && <Loader className="animate-spin text-accent-cyan" />}
        <div ref={endRef} />
      </div>
    </Card>}
  </div>;
}
