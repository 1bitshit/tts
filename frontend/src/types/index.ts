export * from './api';
export * from './audio';
export * from './speaker';
export * from './debate';

export type Language = 'en' | 'zh-cn' | 'de';

export interface VoicePrompt {
  id: string;
  createdAt: string;
}

export interface ToastMessage {
  id: string;
  message: string;
  type: 'success' | 'error' | 'warning' | 'info';
}
