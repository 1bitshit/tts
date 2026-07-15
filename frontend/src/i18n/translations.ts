import { en } from './locales/en/common';
import { zhCN } from './locales/zh-cn/common';
import { de } from './locales/de/common';

export const translations = {
  en,
  'zh-cn': zhCN,
  de,
};

export type TranslationKey = keyof typeof translations.en;
