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
  const [narratorGender, setNarratorGender] = useState<'female' | 'male'>('female');
  const [characterGender, setCharacterGender] = useState<'female' | 'male' | 'mixed'>('mixed');
  const [characterCount, setCharacterCount] = useState(2);
  const [model, setModel] = useState('Qwen/Qwen3-4B-Instruct-2507-GGUF');
  const [deliveryMode, setDeliveryMode] = useState<'live' | 'prerecorded'>('live');
  const [progress, setProgress] = useState({ percent: 0, label: 'Bereit' });
  const [story, setStory] = useState<StoryState | null>(null);
  const [saved, setSaved] = useState<Array<{ session_id: string; title: string; status: string; message_count: number; updated_at: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [ideaBusy, setIdeaBusy] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isReplaying, setIsReplaying] = useState(false);
  const streamRef = useRef<AbortController | null>(null);
  const pollRef = useRef<number | null>(null);
  const seenAudioRef = useRef(new Set<string>());
  const replayAudioRef = useRef<HTMLAudioElement | null>(null);
  const replayCancelledRef = useRef(false);
  const isReplayingRef = useRef(false);
  const liveAudioQueueRef = useRef<Array<{ key: string; timestamp: string; audio: string }>>([]);
  const queuedAudioKeysRef = useRef(new Set<string>());
  const liveAudioRef = useRef<HTMLAudioElement | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const volumeLabel = (volume = 1) => `1.${Math.max(0, volume - 1)}`;

  const refreshSaved = async () => {
    try { setSaved(await storyApi.listStories(apiKey)); } catch { /* API key may not be set yet */ }
  };

  useEffect(() => { void refreshSaved(); }, [apiKey]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [story?.messages]);
  useEffect(() => () => {
    streamRef.current?.abort();
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    replayCancelledRef.current = true;
    replayAudioRef.current?.pause();
    liveAudioRef.current?.pause();
  }, []);

  const playLiveQueue = () => {
    if (isReplayingRef.current || liveAudioRef.current || liveAudioQueueRef.current.length === 0) return;
    const nextClip = liveAudioQueueRef.current.shift()!;
    const audio = new Audio(`data:audio/wav;base64,${nextClip.audio}`);
    liveAudioRef.current = audio;
    const next = () => {
      queuedAudioKeysRef.current.delete(nextClip.key);
      liveAudioRef.current = null;
      playLiveQueue();
    };
    audio.onended = next;
    audio.onerror = next;
    void audio.play().catch(next);
  };

  const enqueueLiveAudio = (message: StoryMessage) => {
    if (!message.audio_base64) return;
    const key = `${message.timestamp}:${message.speaker_id}`;
    if (queuedAudioKeysRef.current.has(key)) return;
    queuedAudioKeysRef.current.add(key);
    liveAudioQueueRef.current.push({ key, timestamp: message.timestamp, audio: message.audio_base64 });
    liveAudioQueueRef.current.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
    playLiveQueue();
  };

  const stopLivePlayback = (clearQueue = false) => {
    liveAudioRef.current?.pause();
    liveAudioRef.current = null;
    if (clearQueue) {
      liveAudioQueueRef.current = [];
      queuedAudioKeysRef.current.clear();
    }
  };

  const stopReplay = (resumeLive = true) => {
    replayCancelledRef.current = true;
    isReplayingRef.current = false;
    replayAudioRef.current?.pause();
    replayAudioRef.current = null;
    setIsReplaying(false);
    if (resumeLive) playLiveQueue();
  };

  const replayFromStart = async () => {
    if (!story) return;
    stopReplay(false);
    stopLivePlayback(true);
    replayCancelledRef.current = false;
    isReplayingRef.current = true;
    setIsReplaying(true);
    const clips = [...story.messages]
      .filter((message) => message.audio_base64)
      .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
    for (const clip of clips) {
      if (replayCancelledRef.current) break;
      await new Promise<void>((resolve) => {
        const audio = new Audio(`data:audio/wav;base64,${clip.audio_base64}`);
        replayAudioRef.current = audio;
        const done = () => { replayAudioRef.current = null; resolve(); };
        audio.onended = done;
        audio.onerror = done;
        void audio.play().catch(done);
      });
    }
    if (!replayCancelledRef.current) {
      isReplayingRef.current = false;
      setIsReplaying(false);
      playLiveQueue();
    }
  };

  const playExclusive = (message: StoryMessage) => {
    if (!message.audio_base64) return;
    stopReplay(false);
    stopLivePlayback(true);
    const audio = new Audio(`data:audio/wav;base64,${message.audio_base64}`);
    liveAudioRef.current = audio;
    const done = () => { liveAudioRef.current = null; playLiveQueue(); };
    audio.onended = done;
    audio.onerror = done;
    void audio.play().catch(done);
  };

  const connectStream = (id: string) => {
    streamRef.current?.abort();
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    seenAudioRef.current = new Set(story?.messages.filter((item) => item.audio_base64).map((item) => `${item.timestamp}:${item.speaker_id}`));
    streamRef.current = storyApi.streamStory(id, (event, data) => {
      if (event === 'status') {
        setStory((current) => current ? { ...current, status: data.status } : current);
        if (['finished', 'stopped', 'disconnected'].includes(data.status)) setIsGenerating(false);
      }
      if (event === 'message') {
        seenAudioRef.current.add(`${data.timestamp}:${data.speaker_id}`);
        setStory((current) => current ? {
          ...current,
          status: 'running',
          current_scene: Math.max(current.current_scene, data.scene),
          messages: [...current.messages.filter((item) => !(item.speaker_id === data.speaker_id && item.scene === data.scene)), data as StoryMessage],
        } : current);
        if (deliveryMode === 'live' && data.audio_base64) enqueueLiveAudio(data as StoryMessage);
      }
      if (event === 'text') setStory((current) => current ? {
        ...current,
        messages: [...current.messages.filter((item) => !(item.speaker_id === data.speaker_id && item.scene === data.scene)), data as StoryMessage],
      } : current);
      if (event === 'progress') setProgress({ percent: data.percent, label: data.label });
      if (event === 'turn') setStory((current) => current ? {
        ...current,
        messages: [...current.messages, {
          speaker_id: data.speaker_id, speaker_name: data.speaker_name,
          text: '[Schreibt weiter …]', audio_base64: null, timestamp: '', scene: data.scene,
        }],
      } : current);
      if (event === 'error') { setIsGenerating(false); toast.showToast(data.message || 'Story-Fehler', 'error'); }
    });
    // Some reverse TCP tunnels buffer long-lived SSE responses. Polling keeps the
    // UI and saved audio reliable without changing the normal low-latency stream.
    pollRef.current = window.setInterval(() => {
      void storyApi.getStory(id, apiKey).then((fresh) => {
        const newAudio = fresh.messages.filter((item) => {
          const key = `${item.timestamp}:${item.speaker_id}`;
          if (!item.audio_base64 || seenAudioRef.current.has(key)) return false;
          seenAudioRef.current.add(key);
          return true;
        });
        setStory(fresh);
        if (fresh.progress && fresh.status === 'running') setProgress(fresh.progress);
        if (newAudio.length > 0) {
          setProgress({ percent: 100, label: `${newAudio.at(-1)?.speaker_name}: Audio fertig und gespeichert` });
          if (deliveryMode === 'live') newAudio.forEach(enqueueLiveAudio);
        } else if (fresh.status === 'running') {
          setProgress((current) => current.percent >= 100 ? { percent: 5, label: 'Nächster Beitrag wird erzeugt' } : current);
        }
        if (['finished', 'stopped'].includes(fresh.status)) {
          setIsGenerating(false);
          if (pollRef.current !== null) window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }).catch(() => { /* next poll retries; SSE may still be healthy */ });
    }, 1500);
  };

  const create = async () => {
    setBusy(true);
    try {
      const created = await storyApi.createStory({
        title, premise, genre, model_name: model, max_scenes: 100,
        delivery_mode: deliveryMode, narrator_gender: narratorGender,
        character_gender: characterGender, character_count: characterCount,
        band_minutes: 23,
      }, apiKey);
      setStory(created);
      await refreshSaved();
      toast.showToast('Geschichte gespeichert und bereit', 'success');
    } catch (error) { toast.showToast((error as Error).message, 'error'); }
    finally { setBusy(false); }
  };

  const generateIdea = async () => {
    setIdeaBusy(true);
    try {
      const idea = await storyApi.generateStoryIdea({
        genre, character_count: characterCount, character_gender: characterGender,
        narrator_gender: narratorGender, model_name: model,
      }, apiKey);
      setTitle(idea.title);
      setPremise(idea.premise);
      toast.showToast('Neue Story-Idee wurde erzeugt', 'success');
    } catch (error) { toast.showToast((error as Error).message, 'error'); }
    finally { setIdeaBusy(false); }
  };

  const start = async () => {
    if (!story) return;
    setIsGenerating(true);
    setProgress({ percent: 1, label: 'Story-Modell wird gestartet' });
    setStory({ ...story, status: 'running' });
    connectStream(story.session_id);
    try { await storyApi.startStory(story.session_id, apiKey); }
    catch (error) { setIsGenerating(false); streamRef.current?.abort(); toast.showToast((error as Error).message, 'error'); }
  };

  const stop = async () => {
    if (!story) return;
    await storyApi.stopStory(story.session_id, apiKey);
    streamRef.current?.abort();
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    pollRef.current = null;
    setIsGenerating(false);
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
        <select value={genre} onChange={(e) => setGenre(e.target.value)} className="px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" aria-label="Genre">
          <option value="Mystery-Fantasy">Mystery-Fantasy</option>
          <option value="Abenteuer">Abenteuer</option>
          <option value="Science-Fiction">Science-Fiction</option>
          <option value="Krimi">Krimi</option>
          <option value="Horror">Horror</option>
          <option value="Romantik">Romantik</option>
          <option value="Historisch">Historisch</option>
          <option value="Märchen">Märchen</option>
        </select>
      </div>
      <textarea value={premise} onChange={(e) => setPremise(e.target.value)} rows={3} className="w-full mt-md px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" />
      <div className="grid md:grid-cols-3 gap-md mt-md">
        <select value={narratorGender} onChange={(e) => setNarratorGender(e.target.value as 'female' | 'male')} className="px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" aria-label="Erzählstimme">
          <option value="female">Erzählstimme: weiblich</option>
          <option value="male">Erzählstimme: männlich</option>
        </select>
        <select value={characterGender} onChange={(e) => setCharacterGender(e.target.value as 'female' | 'male' | 'mixed')} className="px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" aria-label="Figurenstimmen">
          <option value="mixed">Figurenstimmen: gemischt</option>
          <option value="female">Figurenstimmen: weiblich</option>
          <option value="male">Figurenstimmen: männlich</option>
        </select>
        <select value={characterCount} onChange={(e) => setCharacterCount(Number(e.target.value))} className="px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" aria-label="Figurenanzahl">
          {[1, 2, 3, 4, 5, 6].map((count) => <option key={count} value={count}>{count} {count === 1 ? 'Figur' : 'Figuren'}</option>)}
        </select>
      </div>
      <p className="mt-sm text-xs text-text-muted">Ein Band wird als vollständiges Manuskript für ungefähr 23 Minuten Hörzeit vorbereitet.</p>
      {!story && <div className="mt-md">
        <Button variant="secondary" onClick={generateIdea} isLoading={ideaBusy}>✨ Titel & Geschichte mit KI generieren</Button>
      </div>}
      <select value={model} onChange={(e) => setModel(e.target.value)} className="w-full mt-md px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" aria-label="Story-Modell">
        <option value="Qwen/Qwen3-4B-Instruct-2507-GGUF">Qwen3-4B-Instruct-2507 (empfohlen, getestet)</option>
        <option value="Qwen/Qwen3-1.7B-GGUF">Qwen/Qwen3-1.7B-GGUF (klein, eingeschränkte Qualität)</option>
        <option value="Qwen/Qwen3-0.6B-GGUF">Qwen/Qwen3-0.6B-GGUF (experimentell, schwache Langform)</option>
      </select>
      <select value={deliveryMode} onChange={(e) => setDeliveryMode(e.target.value as 'live' | 'prerecorded')} className="w-full mt-md px-md py-sm rounded bg-bg-surface border border-border-subtle text-text-primary" aria-label="Story-Wiedergabemodus">
        <option value="live">Live-Streaming – Beiträge sofort anzeigen und abspielen</option>
        <option value="prerecorded">Vorproduziert – erzeugen und später abspielen</option>
      </select>
      {(busy || isGenerating) && <div className="mt-md" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent}>
        <div className="flex justify-between text-xs text-text-secondary mb-xs"><span>{progress.label}</span><span>{progress.percent}%</span></div>
        <div className="h-3 rounded bg-bg-surface border border-border-subtle overflow-hidden"><div className="h-full bg-accent-cyan transition-all duration-300" style={{ width: `${Math.max(progress.percent, 2)}%` }} /></div>
      </div>}
      <div className="flex gap-sm mt-md flex-wrap">
        {!story && <Button onClick={create} isLoading={busy} icon={Save}>Neue Geschichte</Button>}
        {story && story.status !== 'running' && (story.volume || 1) < 10 && <Button onClick={start} icon={Play}>{story.status === 'finished' ? `Band ${volumeLabel((story.volume || 1) + 1)} erzeugen` : `Band ${volumeLabel(story.volume)} weiter vertonen`}</Button>}
        {story?.status === 'finished' && (story.volume || 1) >= 10 && <span className="px-md py-sm text-accent-cyan">Serie mit Band 1.9 abgeschlossen</span>}
        {story?.status === 'running' && <Button onClick={stop} variant="danger" icon={Square}>Stoppen & speichern</Button>}
        {story && <Button variant="secondary" onClick={() => setStory(null)}>Andere Geschichte</Button>}
        {story && story.messages.some((message) => message.audio_base64) && !isReplaying && <Button variant="secondary" onClick={replayFromStart} icon={Play}>Von Anfang hören</Button>}
        {isReplaying && <Button variant="danger" onClick={() => stopReplay()} icon={Square}>Wiedergabe stoppen</Button>}
      </div>
      {story && <p className="mt-sm text-xs text-text-muted">{story.messages.filter((message) => message.audio_base64).length} Audioclips gespeichert · Live-Audio wird vollständig vorproduziert und bleibt wiederholbar.</p>}
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

    {story && <Card title={`${story.title} · Band ${volumeLabel(story.volume)} · Szene ${story.current_scene}`}>
      <div className="max-h-[620px] overflow-y-auto space-y-md">
        {story.messages.map((message, index) => <div key={`${message.timestamp}-${index}`} className="p-md rounded bg-bg-surface border-l-2 border-accent-cyan">
          <div className="flex items-center gap-sm text-xs text-accent-cyan mb-xs">
            <span>{message.speaker_name}</span><span className="text-text-muted">Szene {message.scene}</span>
            {message.audio_base64 && <button className="ml-auto" onClick={() => playExclusive(message)}><Play size={13} /></button>}
          </div>
          <div className={`whitespace-pre-wrap text-sm ${message.text.startsWith('[Schreibt') ? 'text-text-muted animate-pulse' : 'text-text-primary'}`}>{message.text}</div>
        </div>)}
        {story.status === 'running' && story.messages.length === 0 && <Loader className="animate-spin text-accent-cyan" />}
        <div ref={endRef} />
      </div>
    </Card>}
  </div>;
}
