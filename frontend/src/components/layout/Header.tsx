import React, { useEffect, useState } from 'react';
import { StatusIndicator } from '../ui/StatusIndicator';
import { useI18n } from '../../i18n/I18nContext';
import { checkHealth } from '../../services/api';
import type { Language } from '../../types';

const LANGUAGES: ReadonlyArray<{ code: Language; label: string; name: string }> = [
  { code: 'de', label: 'DE', name: 'Deutsch' },
  { code: 'en', label: 'EN', name: 'English' },
  { code: 'zh-cn', label: '中文', name: '中文' },
];

export function Header() {
  const { language, setLanguage } = useI18n();
  const [serverStatus, setServerStatus] = useState<{
    status: 'online' | 'loading' | 'offline';
    text: string;
  }>({ status: 'loading', text: 'Checking...' });

  useEffect(() => {
    let cancelled = false;

    const loadServerStatus = async () => {
      try {
        const data = await checkHealth();
        if (cancelled) return;
        setServerStatus({ status: 'online', text: `v${data.version}` });
      } catch {
        if (cancelled) return;
        setServerStatus({ status: 'offline', text: 'Offline' });
      }
    };

    void loadServerStatus();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <header className="sticky top-0 z-[100] bg-gradient-to-b from-bg-deep to-bg-deep/95 backdrop-blur-xl border-b border-border-subtle py-lg">
      <div className="max-w-[1200px] mx-auto px-lg">
        <div className="flex items-center justify-between gap-lg flex-wrap">
          {/* Logo */}
          <div className="flex items-center gap-md">
            <div className="w-12 h-12 flex items-center justify-center bg-gradient-to-br from-accent-cyan to-accent-coral rounded-md font-display font-bold text-xl text-bg-deep">
              Q3
            </div>
            <div>
              <div className="font-display text-2xl font-bold bg-gradient-to-r from-accent-cyan to-text-primary bg-clip-text text-transparent">
                Qwen3-TTS
              </div>
              <div className="text-xs text-text-muted font-display">
                Voice Laboratory
              </div>
            </div>
          </div>

          {/* Language Switcher */}
          <div
            className="flex gap-xs p-[3px] bg-bg-surface border border-border-subtle rounded-md"
            role="group"
            aria-label="Language"
          >
            {LANGUAGES.map(({ code, label, name }) => {
              const isActive = language === code;

              return (
                <button
                  key={code}
                  type="button"
                  onClick={() => setLanguage(code)}
                  aria-label={name}
                  aria-pressed={isActive}
                  className={`min-w-10 px-sm py-xs font-display text-xs rounded border transition-all ${
                    isActive
                      ? 'font-bold text-bg-deep bg-accent-cyan border-accent-cyan shadow-glow-cyan'
                      : 'font-medium text-text-muted bg-transparent border-transparent hover:text-text-primary hover:bg-bg-elevated hover:border-border-subtle'
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>

          {/* Status */}
          <StatusIndicator status={serverStatus.status} text={serverStatus.text} />
        </div>
      </div>
    </header>
  );
}
