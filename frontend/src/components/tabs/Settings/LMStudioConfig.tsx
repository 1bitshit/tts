import React, { useState, useEffect, useCallback } from 'react';
import { Cpu, RefreshCw, Download, Wifi, WifiOff } from 'lucide-react';
import { useTranslation } from '../../../i18n/I18nContext';
import { useToast } from '../../../context/ToastContext';
import { CONFIG } from '../../../config/api';

export function LMStudioConfig() {
  const t = useTranslation();
  const toast = useToast();

  const [connected, setConnected] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState(() => localStorage.getItem('debate-model') || '');
  const [downloadId, setDownloadId] = useState('');
  const [loading, setLoading] = useState(false);

  const checkConnection = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${CONFIG.baseUrl}/api/v1/debate/lm/models`, {
        headers: { 'X-API-Key': localStorage.getItem('qwen-tts-api-key') || '' },
      });
      if (resp.ok) {
        const data = await resp.json();
        setModels(data.models || []);
        setConnected(true);
      } else {
        setConnected(false);
        setModels([]);
      }
    } catch {
      setConnected(false);
      setModels([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { checkConnection(); }, [checkConnection]);

  const handleSelectModel = (model: string) => {
    setSelectedModel(model);
    localStorage.setItem('debate-model', model);
    toast.showToast(`Model set to: ${model}`, 'success');
  };

  const handleDownload = async () => {
    if (!downloadId.trim()) return;
    toast.showToast(`Downloading ${downloadId}...`, 'info');
    try {
      const resp = await fetch(`${CONFIG.baseUrl}/api/v1/debate/lm/download`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': localStorage.getItem('qwen-tts-api-key') || '',
        },
        body: JSON.stringify({ model_id: downloadId }),
      });
      if (resp.ok) {
        toast.showToast(`Download started: ${downloadId}`, 'success');
        setDownloadId('');
        setTimeout(checkConnection, 5000);
      } else {
        const err = await resp.json();
        toast.showToast(err.detail || 'Download failed', 'error');
      }
    } catch (e: any) {
      toast.showToast(e.message || 'Download failed', 'error');
    }
  };

  return (
    <div className="p-lg bg-bg-surface border border-border-subtle rounded-lg">
      <h3 className="font-display text-sm font-semibold text-text-primary mb-md flex items-center gap-sm">
        <Cpu className="w-4 h-4" /> {t('lmStudioTitle')}
      </h3>
      <p className="text-sm text-text-secondary mb-md">{t('lmStudioDesc')}</p>

      {/* Status */}
      <div className="flex items-center gap-sm mb-md p-sm rounded-md bg-bg-surface/50">
        {connected ? (
          <Wifi className="w-4 h-4 text-green-400" />
        ) : (
          <WifiOff className="w-4 h-4 text-red-400" />
        )}
        <span className="text-sm">{t('lmStudioStatus')}: </span>
        <span className={`text-sm font-medium ${connected ? 'text-green-400' : 'text-red-400'}`}>
          {connected ? t('lmStudioConnected') : t('lmStudioDisconnected')}
        </span>
        <button
          onClick={checkConnection}
          disabled={loading}
          className="ml-auto p-xs rounded hover:bg-bg-elevated transition-colors"
        >
          <RefreshCw size={14} className={`text-text-secondary ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {!connected && (
        <div className="text-xs text-text-secondary mb-md p-sm rounded-md bg-bg-surface/30">
          {t('lmStudioNotRunning')}
        </div>
      )}

      {/* Model Selection */}
      {connected && (
        <div className="space-y-sm mb-md">
          <label className="text-xs font-medium text-text-secondary">{t('lmStudioModel')}</label>
          <select
            value={selectedModel}
            onChange={e => handleSelectModel(e.target.value)}
            className="w-full px-sm py-xs rounded bg-bg-surface border border-border-subtle text-text-primary text-xs"
          >
            <option value="">{t('lmStudioSelectModel')}</option>
            {models.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          {models.length === 0 && (
            <p className="text-xs text-text-secondary">{t('lmStudioNoModels')}</p>
          )}
        </div>
      )}

      {/* Download Model */}
      <div className="space-y-sm">
        <label className="text-xs font-medium text-text-secondary">{t('lmStudioDownload')}</label>
        <div className="flex gap-sm">
          <input
            type="text"
            value={downloadId}
            onChange={e => setDownloadId(e.target.value)}
            placeholder={t('lmStudioDownloadPlaceholder')}
            className="flex-1 px-sm py-xs rounded bg-bg-surface border border-border-subtle text-text-primary text-xs"
          />
          <button
            onClick={handleDownload}
            disabled={!downloadId.trim()}
            className="px-md py-xs rounded bg-accent-cyan/20 text-accent-cyan text-xs font-medium hover:bg-accent-cyan/30 transition-colors disabled:opacity-50"
          >
            <Download size={12} className="inline mr-xs" />
            {t('lmStudioDownload')}
          </button>
        </div>
      </div>
    </div>
  );
}
