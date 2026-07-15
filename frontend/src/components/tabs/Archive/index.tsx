import React, { useEffect, useState } from 'react';
import { Archive, MessageCircle, Download, Trash2, Play, ChevronDown, ChevronUp, Loader } from 'lucide-react';
import { Card } from '../../ui/Card';
import { Button } from '../../ui/Button';
import { useAppContext } from '../../../context/AppContext';
import { useToast } from '../../../context/ToastContext';
import { useTranslation } from '../../../i18n/I18nContext';
import * as archiveService from '../../../services/archive';

interface ArchivedDebate {
  session_id: string;
  topic: string;
  speakers: { id: string; name: string }[];
  status: string;
  current_round: number;
  max_rounds: number;
  saved_at: string;
  message_count: number;
}

interface ArchivedPrompt {
  id: string;
  name?: string;
  text?: string;
  saved_at: string;
}

interface ArchivedClip {
  id: string;
  name: string;
  path: string;
  size: number;
  created_at: string;
}

export function ArchiveTab() {
  const t = useTranslation();
  const { apiKey } = useAppContext();
  const toast = useToast();

  const [debates, setDebates] = useState<ArchivedDebate[]>([]);
  const [prompts, setPrompts] = useState<ArchivedPrompt[]>([]);
  const [clips, setClips] = useState<ArchivedClip[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [d, p, c] = await Promise.all([
        archiveService.listDebates(apiKey),
        archiveService.listPrompts(apiKey),
        archiveService.listClips(apiKey),
      ]);
      setDebates(d);
      setPrompts(p);
      setClips(c);
    } catch (e: any) {
      toast.showToast(e.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleDelete = async (sessionId: string) => {
    try {
      await archiveService.deleteDebate(sessionId, apiKey);
      setDebates((prev) => prev.filter((d) => d.session_id !== sessionId));
    } catch (e: any) {
      toast.showToast(e.message, 'error');
    }
  };

  const handleDeletePrompt = async (promptId: string) => {
    try {
      const resp = await fetch(`${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/archive/prompts/${promptId}`, {
        method: 'DELETE',
        headers: { 'X-API-Key': apiKey },
      });
      if (!resp.ok) throw new Error('Failed to delete prompt');
      setPrompts((prev) => prev.filter((p) => p.id !== promptId));
    } catch (e: any) {
      toast.showToast(e.message, 'error');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-3xl">
        <Loader className="w-6 h-6 animate-spin text-accent-cyan" />
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-sm mb-lg">
        <Archive className="w-5 h-5 text-accent-cyan" />
        <span className="font-display text-sm text-text-secondary">{t('archiveDesc')}</span>
        <div className="ml-auto flex gap-sm">
          <Button variant="secondary" onClick={load}>
            <Loader className="w-3.5 h-3.5" />
            {t('lmStudioRefresh')}
          </Button>
          <a href={archiveService.downloadZipUrl()} download>
            <Button variant="secondary">
              <Download className="w-3.5 h-3.5" />
              {t('archiveDownloadZip')}
            </Button>
          </a>
        </div>
      </div>
      <Card className="mb-lg">
        <h3 className="font-display text-sm font-semibold text-text-primary mb-md flex items-center gap-sm">
          <MessageCircle className="w-4 h-4 text-accent-cyan" />
          {t('archiveDebates')}
        </h3>
        {debates.length === 0 ? (
          <p className="text-text-muted text-sm">{t('archiveNoDebates')}</p>
        ) : (
          <div className="space-y-sm">
            {debates.map((d) => (
              <div key={d.session_id} className="bg-bg-surface border border-border-subtle rounded-md overflow-hidden">
                <button
                  onClick={() => setExpanded(expanded === d.session_id ? null : d.session_id)}
                  className="w-full flex items-center justify-between p-md text-left hover:bg-bg-surface/80 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-display text-sm text-text-primary truncate">{d.topic}</div>
                    <div className="text-xs text-text-muted mt-xs">
                      {d.speakers.map((s) => s.name).join(', ')} — {d.message_count} {t('archiveMessages')}, {d.current_round}/{d.max_rounds} {t('archiveRounds')}
                    </div>
                    <div className="text-[0.625rem] text-text-muted mt-xs">
                      {t('archiveSavedAt')}: {new Date(d.saved_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="flex items-center gap-sm shrink-0 ml-md">
                    {expanded === d.session_id ? <ChevronUp className="w-4 h-4 text-text-muted" /> : <ChevronDown className="w-4 h-4 text-text-muted" />}
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDelete(d.session_id); }}
                      className="text-text-muted hover:text-accent-coral p-xs"
                      title={t('archiveDeleteDebate')}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </button>
                {expanded === d.session_id && (
                  <div className="px-md pb-md border-t border-border-subtle pt-sm">
                    <pre className="text-xs text-text-secondary whitespace-pre-wrap max-h-60 overflow-y-auto">
                      {JSON.stringify(d, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Voice Prompts */}
      <Card className="mb-lg">
        <h3 className="font-display text-sm font-semibold text-text-primary mb-md flex items-center gap-sm">
          <Play className="w-4 h-4 text-accent-cyan" />
          {t('archivePrompts')}
        </h3>
        {prompts.length === 0 ? (
          <p className="text-text-muted text-sm">{t('archiveNoPrompts')}</p>
        ) : (
          <div className="space-y-sm">
            {prompts.map((p) => (
              <div key={p.id} className="flex items-center justify-between p-md bg-bg-surface border border-border-subtle rounded-md">
                <div className="flex-1 min-w-0">
                  <div className="font-display text-xs text-text-primary truncate">{p.name || p.id}</div>
                  <div className="text-[0.625rem] text-text-muted">{new Date(p.saved_at).toLocaleString()}</div>
                </div>
                <button
                  onClick={() => handleDeletePrompt(p.id)}
                  className="text-text-muted hover:text-accent-coral p-xs shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Audio Clips */}
      <Card className="mb-lg">
        <h3 className="font-display text-sm font-semibold text-text-primary mb-md flex items-center gap-sm">
          <Download className="w-4 h-4 text-accent-cyan" />
          {t('archiveClips')}
        </h3>
        {clips.length === 0 ? (
          <p className="text-text-muted text-sm">{t('archiveNoClips')}</p>
        ) : (
          <div className="space-y-sm">
            {clips.map((c) => (
              <div key={c.id} className="flex items-center justify-between p-md bg-bg-surface border border-border-subtle rounded-md">
                <div className="flex-1 min-w-0">
                  <div className="font-display text-xs text-text-primary truncate">{c.name}</div>
                  <div className="text-[0.625rem] text-text-muted">{new Date(c.created_at).toLocaleString()} — {(c.size / 1024).toFixed(1)} KB</div>
                </div>
                <Play
                  className="w-4 h-4 text-accent-cyan cursor-pointer hover:text-accent-cyan/80 shrink-0"
                  onClick={() => {
                    const audio = new Audio(`${import.meta.env.VITE_API_BASE_URL || ''}/api/v1/archive/clips/${c.id}/audio`);
                    audio.play();
                  }}
                />
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
