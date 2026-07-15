import React, { useState, useRef, useCallback, useEffect } from 'react';
import { Play, Square, Plus, Trash2, MessageCircle, Upload, Mic, SkipForward, Music, Loader } from 'lucide-react';
import { Card } from '../../ui/Card';
import { Button } from '../../ui/Button';
import { useAppContext } from '../../../context/AppContext';
import { useToast } from '../../../context/ToastContext';
import { useTranslation } from '../../../i18n/I18nContext';
import * as debateService from '../../../services/debate';
import { getHeaders } from '../../../services/api';
import type { SpeakerConfig, DebateMessage as DebateMessageType } from '../../../types/debate';

const DEFAULT_PERSONALITIES: Record<string, string> = {
  progressive: 'Du bist eine leidenschaftliche progressive Debattantin. Du argumentierst für soziale Gerechtigkeit, Umweltschutz und evidenzbasierte Politik. Nutze logische Argumente und nenne Beispiele aus der Praxis.',
  conservative: 'Du bist ein scharfsinniger konservativer Debattant. Du argumentierst für freie Märkte, individuelle Freiheit und traditionelle Werte. Nutze logische Argumente und nenne Beispiele aus der Praxis.',
  libertarian: 'Du bist ein libertärer Debattant. Du argumentierst für maximale individuelle Freiheit, minimale staatliche Eingriffe und persönliche Verantwortung.',
  centrist: 'Du bist ein pragmatischer zentristischer Debattant. Du bewertest jedes Argument nach seinen Vorzügen und suchst nach ausgewogenen Lösungen.',
  skeptic: 'Du bist ein skeptischer Debattant. Du hinterfragst Annahmen, forderst Beweise und stellst die Prämissen jedes Arguments in Frage.',
  analyst: 'Du bist ein datengetriebener Analysten-Debattant. Du konzentrierst dich auf Statistiken, Forschungsergebnisse und empirische Belege.',
};

const DEFAULT_VOICES = [
  { nameKey: 'voiceWarmFemale', desc: 'Eine warme, artikulierte Frauenstimme mit einem selbstbewussten, überzeugenden Ton' },
  { nameKey: 'voiceCalmMale', desc: 'Eine ruhige, autoritäre Männerstimme mit einem bedachten, gemessenen Ton' },
  { nameKey: 'voiceEnergeticFemale', desc: 'Eine energische, leidenschaftliche Frauenstimme mit dynamischer Bandbreite' },
  { nameKey: 'voiceDeepMale', desc: 'Eine tiefe, resonante Männerstimme mit einem nachdenklichen, gelehrten Ton' },
  { nameKey: 'voiceBrightFemale', desc: 'Eine helle, junge Frauenstimme mit klarer Artikulation' },
  { nameKey: 'voiceNeutralMale', desc: 'Eine neutrale, professionelle Männerstimme, geeignet für formelle Debatten' },
];

const EMOJI_MAP: Record<string, string> = {
  '(happy)': '😊', '(sad)': '😢', '(angry)': '😠', '(surprised)': '😮',
  '(calm)': '😌', '(excited)': '🤩', '(thoughtful)': '🤔', '(confident)': '💪',
  '(sarcastic)': '😏', '(laughing)': '😂', '(serious)': '🧐', '(whispering)': '🤫',
  '(shouting)': '📢', '(fearful)': '😨', '(playful)': '😜', '(warm)': '🥰',
  '(cold)': '🥶', '(confused)': '😕', '(relieved)': '😮‍💨', '(tense)': '😬',
  '(soft)': '🤗', '(joyful)': '🎉', '(crying)': '😭', '(breathless)': '😤',
  '(mock_angry)': '😡', '(bored)': '🥱', '(romantic)': '💕', '(formal)': '🎩',
  '(casual)': '😎', '(fast)': '⚡', '(slow)': '🐢', '(pause)': '⏸️',
  '(sigh)': '💨', '(breath)': '🌬️',
};

function stripEmotionTags(text: string): string {
  return text.replace(/\((happy|sad|angry|surprised|calm|excited|thoughtful|confident|sarcastic|laughing|serious|whispering|shouting|fearful|playful|warm|cold|confused|relieved|tense|soft|joyful|crying|breathless|mock_angry|bored|romantic|formal|casual|fast|slow|pause|sigh|breath)(:\d*\.?\d+)?\)/g, '');
}

function renderWithEmojis(text: string): React.ReactNode {
  let result = text;
  for (const [tag, emoji] of Object.entries(EMOJI_MAP)) {
    result = result.replace(new RegExp(tag.replace('(', '\\(').replace(')', '\\)') + '(:\\d*\\.?\\d+)?', 'g'), emoji);
  }
  return result;
}

interface UploadedClip {
  id: string;
  name: string;
  url: string;
  duration: number;
}

export function DebateTab() {
  const { apiKey } = useAppContext();
  const toast = useToast();
  const t = useTranslation();

  const [topic, setTopic] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'creating_voices' | 'running' | 'stopped' | 'finished' | 'disconnected'>('idle');
  const [speakers, setSpeakers] = useState<SpeakerConfig[]>([]);
  const [messages, setMessages] = useState<DebateMessageType[]>([]);
  const [lmConnected, setLmConnected] = useState(true);
  const [abortController, setAbortController] = useState<AbortController | null>(null);
  const [newSpeakerName, setNewSpeakerName] = useState('');
  const [newSpeakerPersonality, setNewSpeakerPersonality] = useState('');
  const [newSpeakerVoice, setNewSpeakerVoice] = useState(DEFAULT_VOICES[0].desc);
  const [uploadedClips, setUploadedClips] = useState<UploadedClip[]>([]);
  const [clipsExpanded, setClipsExpanded] = useState(false);
  const [liveAudioQueue, setLiveAudioQueue] = useState<string[]>([]);
  const [autoPlay, setAutoPlay] = useState(true);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const audioRefs = useRef<Map<string, HTMLAudioElement>>(new Map());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const currentlyPlaying = useRef<string | null>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-play new messages
  useEffect(() => {
    if (!autoPlay || messages.length === 0) return;
    const last = messages[messages.length - 1];
    if (last.audio_base64 && !last.text.includes('[is thinking')) {
      const id = `${last.speaker_id}_${messages.length - 1}`;
      setTimeout(() => playAudio(last.audio_base64!, id), 300);
    }
  }, [messages, autoPlay]);

  const handleCreate = useCallback(async () => {
    if (!topic.trim()) {
      toast.showToast(t('debateNoTopic'), 'warning');
      return;
    }
    if (speakers.length < 2) {
      toast.showToast(t('debateMinSpeakers'), 'warning');
      return;
    }
    try {
      const result = await debateService.createDebate({ topic, speakers, max_rounds: 10, auto_advance: true, delay_between_speakers: 1.5 }, apiKey);
      setSessionId(result.session_id);
      setMessages([]);
      setStatus('idle');
      toast.showToast(t('debateCreated'), 'success');
    } catch (e: any) {
      toast.showToast(e.message || t('debateCreateError'), 'error');
    }
  }, [topic, speakers, apiKey, toast, t]);

  const handleStart = useCallback(async () => {
    if (!sessionId) return;
    const controller = debateService.streamDebate(sessionId, (event, data) => {
      switch (event) {
        case 'status':
          setStatus(data.status);
          if (data.lm_studio_connected !== undefined) setLmConnected(data.lm_studio_connected);
          break;
        case 'turn':
          setMessages(prev => [...prev, { speaker_id: data.speaker_id, speaker_name: data.speaker_name, text: `[${data.speaker_name} ${t('debateThinking')}]`, audio_base64: null, timestamp: '', round: data.round }]);
          break;
        case 'message':
          setMessages(prev => {
            const newMsgs = [...prev];
            if (newMsgs.length > 0 && newMsgs[newMsgs.length - 1].text.includes(t('debateThinking'))) {
              newMsgs[newMsgs.length - 1] = { speaker_id: data.speaker_id, speaker_name: data.speaker_name, text: data.text, audio_base64: data.audio_base64, timestamp: data.timestamp, round: data.round };
            } else {
              newMsgs.push({ speaker_id: data.speaker_id, speaker_name: data.speaker_name, text: data.text, audio_base64: data.audio_base64, timestamp: data.timestamp, round: data.round });
            }
            return newMsgs;
          });
          break;
        case 'error':
          toast.showToast(data.message || 'Debate error', 'error');
          break;
      }
    });
    setAbortController(controller);
    setStatus('creating_voices');
    try {
      await debateService.startDebate(sessionId, apiKey);
    } catch (e: any) {
      toast.showToast(e.message || t('debateStartError'), 'error');
      controller.abort();
      setStatus('idle');
    }
  }, [sessionId, apiKey, toast, t]);

  const handleStop = useCallback(async () => {
    if (!sessionId) return;
    try { await debateService.stopDebate(sessionId, apiKey); } catch { }
    abortController?.abort();
    setStatus('stopped');
  }, [sessionId, apiKey, abortController]);

  const addSpeaker = () => {
    if (speakers.length >= 6) { toast.showToast(t('debateMaxSpeakers'), 'warning'); return; }
    const name = newSpeakerName || `${t('debateAddSpeaker')} ${speakers.length + 1}`;
    setSpeakers(prev => [...prev, { id: `speaker_${prev.length}`, name, personality: newSpeakerPersonality || DEFAULT_PERSONALITIES.progressive, model_name: '', voice_description: newSpeakerVoice, language: 'German', voice_prompt_id: '' }]);
    setNewSpeakerName('');
  };

  const removeSpeaker = (id: string) => {
    if (speakers.length <= 2) { toast.showToast(t('debateMinSpeakersRemove'), 'warning'); return; }
    setSpeakers(prev => prev.filter(s => s.id !== id));
  };

  const playAudio = (audioBase64: string, msgId: string) => {
    if (currentlyPlaying.current === msgId) {
      const existing = audioRefs.current.get(msgId);
      if (existing) { existing.pause(); existing.currentTime = 0; currentlyPlaying.current = null; return; }
    }
    const existing = audioRefs.current.get(msgId);
    if (existing) { existing.currentTime = 0; existing.play(); currentlyPlaying.current = msgId; return; }
    const audio = new Audio(`data:audio/wav;base64,${audioBase64}`);
    audioRefs.current.set(msgId, audio);
    audio.play();
    currentlyPlaying.current = msgId;
    audio.onended = () => { currentlyPlaying.current = null; };
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!['audio/wav', 'audio/mpeg', 'audio/mp3', 'audio/x-wav', 'audio/wave'].includes(file.type) && !file.name.match(/\.(wav|mp3)$/i)) {
      toast.showToast('Nur WAV/MP3 Dateien', 'warning');
      return;
    }
    const url = URL.createObjectURL(file);
    const audioEl = new Audio(url);
    audioEl.onloadedmetadata = () => {
      setUploadedClips(prev => [...prev, { id: Date.now().toString(), name: file.name, url, duration: audioEl.duration }]);
      toast.showToast(`Audio geladen: ${file.name}`, 'success');
    };
  };

  const playClip = (clip: UploadedClip) => {
    const audio = new Audio(clip.url);
    audio.play();
  };

  const playAllAudio = () => {
    const audioMsgs = messages.filter(m => m.audio_base64 && !m.text.includes('[is thinking'));
    audioMsgs.forEach((m, i) => {
      setTimeout(() => playAudio(m.audio_base64!, `${m.speaker_id}_stream_${i}`), i * 3000);
    });
  };

  const speakerColors = ['#00d4ff', '#ff6b9d', '#ffd93d', '#6bcb77', '#a66cff', '#ff8c42'];

  return (
    <div className="space-y-lg">
      {/* Controls */}
      <Card title={t('debateTitle')} icon={MessageCircle}>
        <div className="space-y-md">
          <div className="flex gap-sm">
            <input type="text" value={topic} onChange={e => setTopic(e.target.value)} placeholder={t('debateTopicPlaceholder')}
              className="flex-1 px-md py-sm rounded-md bg-bg-surface border border-border-subtle text-text-primary font-mono text-sm focus:outline-none focus:border-accent-cyan"
              disabled={status === 'running'} />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-sm">
            {speakers.map((s, i) => (
              <div key={s.id} className="p-sm rounded-md bg-bg-surface/50 border border-border-subtle relative group">
                <div className="flex items-center gap-sm mb-xs">
                  <div className="w-3 h-3 rounded-full" style={{ backgroundColor: speakerColors[i % speakerColors.length] }} />
                  <span className="text-sm font-medium text-text-primary">{s.name}</span>
                  <button onClick={() => removeSpeaker(s.id)} className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity"><Trash2 size={14} className="text-text-secondary hover:text-red-400" /></button>
                </div>
                <div className="text-xs text-text-secondary truncate">{s.personality.slice(0, 60)}...</div>
              </div>
            ))}
            {speakers.length < 6 && (
              <div className="p-sm rounded-md border border-dashed border-border-subtle flex items-center justify-center">
                <button onClick={addSpeaker} className="flex items-center gap-xs text-xs text-text-secondary hover:text-accent-cyan transition-colors"><Plus size={14} /> {t('debateAddSpeaker')}</button>
              </div>
            )}
          </div>
          <details className="text-sm">
            <summary className="text-accent-cyan cursor-pointer hover:opacity-80">+ {t('debateConfigureSpeaker')}</summary>
            <div className="mt-sm space-y-sm p-sm rounded-md bg-bg-surface/30">
              <div className="grid grid-cols-2 gap-sm">
                <input type="text" value={newSpeakerName} onChange={e => setNewSpeakerName(e.target.value)} placeholder={t('debateSpeakerName')} className="px-sm py-xs rounded bg-bg-surface border border-border-subtle text-text-primary text-xs" />
                <select value={newSpeakerPersonality} onChange={e => setNewSpeakerPersonality(e.target.value)} className="px-sm py-xs rounded bg-bg-surface border border-border-subtle text-text-primary text-xs">
                  <option value="">{t('debatePersonalityType')}</option>
                  {Object.entries(DEFAULT_PERSONALITIES).map(([k, v]) => (<option key={k} value={v}>{t(k as any)}</option>))}
                </select>
              </div>
              <select value={newSpeakerVoice} onChange={e => setNewSpeakerVoice(e.target.value)} className="w-full px-sm py-xs rounded bg-bg-surface border border-border-subtle text-text-primary text-xs">
                {DEFAULT_VOICES.map(v => (<option key={v.nameKey} value={v.desc}>{t(v.nameKey as any)} — {v.desc}</option>))}
              </select>
            </div>
          </details>
          <div className="flex gap-sm items-center flex-wrap">
            {!sessionId ? (
              <Button onClick={handleCreate} disabled={!topic.trim() || speakers.length < 2}>{t('debateCreate')}</Button>
            ) : status === 'idle' || status === 'stopped' || status === 'finished' ? (
              <Button onClick={handleStart} icon={Play}>{status === 'finished' ? t('debateReplay') : t('debateStart')}</Button>
            ) : (
              <Button onClick={handleStop} icon={Square} variant="secondary">{t('debateStop')}</Button>
            )}
            {!lmConnected && status === 'running' && <span className="text-xs text-red-400">⚠️ {t('debateLmDisconnected')}</span>}
            {status === 'creating_voices' && <span className="text-xs text-accent-cyan animate-pulse"><Loader size={12} className="inline animate-spin mr-xs" />{t('debateCreatingVoices')}</span>}

            {/* Auto-play toggle */}
            <label className="flex items-center gap-xs text-xs text-text-secondary ml-auto">
              <input type="checkbox" checked={autoPlay} onChange={e => setAutoPlay(e.target.checked)} className="rounded" />
              Live-Audio
            </label>

            {/* Play all */}
            {messages.filter(m => m.audio_base64).length > 0 && (
              <button onClick={playAllAudio} className="flex items-center gap-xs text-xs text-accent-cyan hover:opacity-80">
                <SkipForward size={12} /> Alle abspielen
              </button>
            )}
          </div>
        </div>
      </Card>

      {/* Pre-recorded Audio Clips */}
      <Card title="Audio-Clips" icon={Music}>
        <div className="space-y-sm">
          <div className="flex gap-sm items-center">
            <input ref={fileInputRef} type="file" accept=".wav,.mp3,audio/wav,audio/mpeg" onChange={handleFileUpload} className="hidden" />
            <button onClick={() => fileInputRef.current?.click()} className="flex items-center gap-xs text-xs px-md py-sm rounded-md bg-bg-surface border border-border-subtle hover:border-accent-cyan transition-colors">
              <Upload size={14} /> WAV/MP3 hochladen
            </button>
            <button onClick={() => setClipsExpanded(!clipsExpanded)} className="text-xs text-text-secondary hover:text-text-primary">
              {clipsExpanded ? '▼' : '▶'} {uploadedClips.length} Clips
            </button>
          </div>
          {clipsExpanded && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-xs max-h-40 overflow-y-auto">
              {uploadedClips.length === 0 && <div className="text-xs text-text-secondary p-sm">Keine Clips geladen</div>}
              {uploadedClips.map(clip => (
                <div key={clip.id} className="flex items-center gap-sm p-xs rounded bg-bg-surface/30 hover:bg-bg-surface/50">
                  <button onClick={() => playClip(clip)} className="p-xs rounded hover:bg-bg-surface"><Play size={12} className="text-accent-cyan" /></button>
                  <span className="text-xs text-text-primary truncate flex-1">{clip.name}</span>
                  <span className="text-[10px] text-text-secondary">{Math.round(clip.duration)}s</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {/* Live Debate Chat */}
      {sessionId && (
        <Card title={
          <span className="flex items-center gap-sm">
            {status === 'running' ? <><Loader size={14} className="animate-spin text-accent-cyan" /> Live-Debatte</> : 'Debatten-Transkript'}
            {status === 'running' && <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />}
          </span>
        }>
          <div className="space-y-md max-h-[500px] overflow-y-auto pr-sm" style={{ scrollBehavior: 'smooth' }}>
            {messages.length === 0 && <div className="text-center text-text-secondary text-sm py-xl">{t('debateNotStarted')}</div>}
            {messages.map((msg, i) => {
              const speakerIdx = speakers.findIndex(s => s.id === msg.speaker_id);
              const color = speakerColors[speakerIdx >= 0 ? speakerIdx % speakerColors.length : 0];
              const isThinking = msg.text.includes(t('debateThinking'));
              return (
                <div key={i} className="flex gap-sm group">
                  <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold ${isThinking ? 'animate-pulse' : ''}`}
                    style={{ backgroundColor: color + '33', color }}>
                    {msg.speaker_name[0]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-xs mb-xs">
                      <span className="text-xs font-semibold" style={{ color }}>{msg.speaker_name}</span>
                      <span className="text-[10px] text-text-secondary">{t('debateRounds')} {msg.round}</span>
                      {msg.audio_base64 && (
                        <button onClick={() => playAudio(msg.audio_base64!, `${msg.speaker_id}_${i}`)}
                          className={`ml-auto p-xs rounded transition-colors ${currentlyPlaying.current === `${msg.speaker_id}_${i}` ? 'bg-accent-cyan/20 text-accent-cyan' : 'text-text-secondary hover:text-accent-cyan'}`}>
                          <Play size={12} />
                        </button>
                      )}
                    </div>
                    {isThinking ? (
                      <div className="text-sm text-text-secondary italic animate-pulse flex items-center gap-xs">
                        <Loader size={12} className="animate-spin" /> {msg.text}
                      </div>
                    ) : (
                      <div className="text-sm text-text-primary whitespace-pre-wrap">{renderWithEmojis(stripEmotionTags(msg.text))}</div>
                    )}
                  </div>
                </div>
              );
            })}
            <div ref={chatEndRef} />
          </div>
        </Card>
      )}

      {/* Stats */}
      {messages.length > 0 && (
        <Card title={t('debateStats')}>
          <div className="flex gap-lg text-sm flex-wrap">
            <div><span className="text-text-secondary">{t('debateMessages')}:</span> <span className="text-text-primary font-medium">{messages.filter(m => !m.text.includes(t('debateThinking'))).length}</span></div>
            <div><span className="text-text-secondary">{t('debateRounds')}:</span> <span className="text-text-primary font-medium">{Math.max(...messages.map(m => m.round), 0)}</span></div>
            <div><span className="text-text-secondary">{t('debateSpeakers')}:</span> <span className="text-text-primary font-medium">{speakers.length}</span></div>
            <div><span className="text-text-secondary">{t('debateStatus')}:</span> <span className="font-medium" style={{ color: status === 'running' ? '#00d4ff' : status === 'finished' ? '#6bcb77' : '#ff6b9d' }}>{status}</span></div>
            <div><span className="text-text-secondary">Audio:</span> <span className="text-text-primary font-medium">{messages.filter(m => m.audio_base64).length} Nachrichten</span></div>
          </div>
        </Card>
      )}
    </div>
  );
}
