import React, { useState } from 'react';
import { FormTextarea } from '../../forms/FormTextarea';
import { FormSelect } from '../../forms/FormSelect';
import { FormInput } from '../../forms/FormInput';
import { RangeSlider } from '../../forms/RangeSlider';
import { Button } from '../../ui/Button';
import { AudioPlayer } from '../../audio/AudioPlayer';
import { SpeakerGrid } from './SpeakerGrid';
import { QuickInstructions } from './QuickInstructions';
import { useAppContext } from '../../../context/AppContext';
import { useToast } from '../../../context/ToastContext';
import { useTranslation } from '../../../i18n/I18nContext';
import { generateCustomVoice, streamEngineSpeech } from '../../../services/api';
import { base64ToBlob } from '../../../utils/audio';

export function CustomVoiceTab() {
  const t = useTranslation();
  const { apiKey, selectedSpeaker, setSelectedSpeaker, customVoiceAudio, setCustomVoiceAudio } = useAppContext();
  const { showToast } = useToast();

  const [text, setText] = useState(t('defaultTextCustomVoice'));
  const [language, setLanguage] = useState('English');
  const [instruct, setInstruct] = useState('');
  const [speed, setSpeed] = useState(1.0);
  const [emotion, setEmotion] = useState('dramatic');
  const [volume, setVolume] = useState(1.0);
  const [deliveryMode, setDeliveryMode] = useState<'wav' | 'stream'>('wav');
  const [temperature, setTemperature] = useState(1.1);
  const [topP, setTopP] = useState(1.0);
  const [repPenalty, setRepPenalty] = useState(1.08);

  const handleGenerate = async () => {
    if (!text.trim()) {
      showToast(t('noText'), 'warning');
      return;
    }

    if (!apiKey) {
      showToast(t('noApiKey'), 'warning');
      return;
    }

    setCustomVoiceAudio({ ...customVoiceAudio, isLoading: true });
    const startTime = performance.now();

    try {
      if (deliveryMode === 'stream') {
        const context = new AudioContext({ sampleRate: 24000 });
        let nextStart = context.currentTime + 0.08;
        await streamEngineSpeech({
          text, language, speaker: selectedSpeaker, instruct: instruct || undefined,
          speed, emotion, volume, temperature, top_p: topP, rep_penalty: repPenalty,
        }, apiKey, (chunk) => {
          const sampleCount = Math.floor(chunk.byteLength / 2);
          if (!sampleCount) return;
          const audioBuffer = context.createBuffer(1, sampleCount, 24000);
          const channel = audioBuffer.getChannelData(0);
          const view = new DataView(chunk.buffer, chunk.byteOffset, chunk.byteLength);
          for (let i = 0; i < sampleCount; i += 1) channel[i] = view.getInt16(i * 2, true) / 32768;
          const source = context.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(context.destination);
          nextStart = Math.max(nextStart, context.currentTime + 0.03);
          source.start(nextStart);
          nextStart += audioBuffer.duration;
        });
        setCustomVoiceAudio({ ...customVoiceAudio, isLoading: false });
        showToast('Live-Streaming abgeschlossen', 'success');
        return;
      }
      const { data, headers } = await generateCustomVoice(
        {
          text,
          language,
          speaker: selectedSpeaker,
          instruct: instruct || undefined,
          speed,
          emotion,
          volume,
          temperature,
          top_p: topP,
          rep_penalty: repPenalty,
          response_format: 'base64',
        },
        apiKey
      );

      const genTime = (performance.now() - startTime) / 1000;
      const audioBlob = base64ToBlob(data.audio, 'audio/wav');
      const url = URL.createObjectURL(audioBlob);

      setCustomVoiceAudio({
        url,
        metrics: {
          generationTime: genTime,
          audioDuration: parseFloat(headers.get('x-audio-duration') || '0'),
          rtf: parseFloat(headers.get('x-rtf') || '0'),
        },
        isLoading: false,
      });

      showToast(t('generated'), 'success');
    } catch (error) {
      showToast((error as Error).message, 'error');
      setCustomVoiceAudio({ ...customVoiceAudio, isLoading: false });
    }
  };

  return (
    <div>
      <div className="mb-xl">
        <h1 className="text-2xl mb-sm text-text-primary">{t('cvTitle')}</h1>
        <p className="text-text-secondary text-base max-w-[600px]">{t('cvDesc')}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-xl">
        {/* Left Column */}
        <div>
          <FormTextarea
            label={t('textToSynth')}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t('textPlaceholder')}
            maxLength={1000}
          />
          <div className="mb-lg" />

          <FormSelect
            label={t('language')}
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
          >
            <option value="Auto">{t('langAuto')}</option>
            <option value="Chinese">{t('langChinese')}</option>
            <option value="English">{t('langEnglish')}</option>
            <option value="Japanese">{t('langJapanese')}</option>
            <option value="Korean">{t('langKorean')}</option>
            <option value="German">{t('langGerman')}</option>
            <option value="French">{t('langFrench')}</option>
            <option value="Russian">{t('langRussian')}</option>
            <option value="Portuguese">{t('langPortuguese')}</option>
            <option value="Spanish">{t('langSpanish')}</option>
            <option value="Italian">{t('langItalian')}</option>
          </FormSelect>
          <div className="mb-lg" />

          <div className="mb-lg">
            <FormInput
              label={t('styleInstruction')}
              value={instruct}
              onChange={(e) => setInstruct(e.target.value)}
              placeholder={t('stylePlaceholder')}
            />
            <QuickInstructions onSelect={setInstruct} />
          </div>

          <RangeSlider
            label={t('speed')}
            value={speed}
            onChange={(e) => setSpeed(parseFloat(e.target.value))}
            min={0.5}
            max={2}
            step={0.1}
          />
          <div className="mb-lg" />
          <FormSelect label="Emotion (native C-Engine)" value={emotion} onChange={(e) => setEmotion(e.target.value)}>
            {['neutral','calm','joy','excited','proud','dramatic','sad','gloomy','annoyed','stern','anger','fear','disgust','surprise','contempt','awe','nostalgia','remorse','outrage','despair'].map((value) => <option key={value} value={value}>{value}</option>)}
          </FormSelect>
          <div className="mb-lg" />
          <RangeSlider label="Lautstärke" value={volume} onChange={(e) => setVolume(parseFloat(e.target.value))} min={0} max={2} step={0.05} formatValue={(value) => `${Math.round(value * 100)}%`} />
          <div className="mb-lg" />
          <FormSelect label="Ausgabe" value={deliveryMode} onChange={(e) => setDeliveryMode(e.target.value as 'wav' | 'stream')}>
            <option value="wav">WAV – vollständig, Tempo exakt</option>
            <option value="stream">Live PCM – Ton während der Erzeugung</option>
          </FormSelect>
          <div className="mt-md p-md rounded bg-bg-surface border border-border-subtle text-xs text-text-muted">
            Native Tags im Text: [calm], [fear], [joy], [pause:500ms], [laugh], [sigh]
          </div>
          <details className="mt-md p-md rounded bg-bg-surface border border-border-subtle">
            <summary className="cursor-pointer text-sm text-accent-cyan">Erweiterte Engine-Einstellungen</summary>
            <div className="space-y-md mt-md">
              <RangeSlider label="Temperatur" value={temperature} onChange={(e) => setTemperature(parseFloat(e.target.value))} min={0} max={2} step={0.05} formatValue={(value) => value.toFixed(2)} />
              <RangeSlider label="Top P" value={topP} onChange={(e) => setTopP(parseFloat(e.target.value))} min={0.1} max={1} step={0.05} formatValue={(value) => value.toFixed(2)} />
              <RangeSlider label="Wiederholungsstrafe" value={repPenalty} onChange={(e) => setRepPenalty(parseFloat(e.target.value))} min={0.5} max={2} step={0.05} formatValue={(value) => value.toFixed(2)} />
            </div>
          </details>

          <Button
            variant="primary"
            isLoading={customVoiceAudio.isLoading}
            loadingText={t('generating')}
            onClick={handleGenerate}
            className="w-full mt-lg"
          >
            <span>▶</span> {t('generateSpeech')}
          </Button>
        </div>

        {/* Right Column */}
        <div>
          <SpeakerGrid
            selectedSpeaker={selectedSpeaker}
            onSelectSpeaker={setSelectedSpeaker}
          />
        </div>
      </div>

      <AudioPlayer
        audioUrl={customVoiceAudio.url}
        metrics={customVoiceAudio.metrics}
        title={t('generatedAudio')}
      />
    </div>
  );
}
