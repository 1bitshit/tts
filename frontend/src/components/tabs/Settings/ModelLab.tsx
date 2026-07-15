import React, { useEffect, useState } from 'react';
import { Download, FlaskConical, Star } from 'lucide-react';
import { useAppContext } from '../../../context/AppContext';
import { getHeaders } from '../../../services/api';
import { CONFIG } from '../../../config/api';
import { Button } from '../../ui/Button';

type Model = { id: string; size: string; tier: string; source?: string; reason: string; rating: { count: number; average: number | null } };
type Recommendations = { date: string; debate: Model[]; story: Model[] };

export function ModelLab() {
  const { apiKey } = useAppContext();
  const [data, setData] = useState<Recommendations | null>(null);
  const [result, setResult] = useState('');
  const [busy, setBusy] = useState('');
  const base = `${CONFIG.baseUrl}/api/v1/model-lab`;

  const request = async (path: string, body?: object) => {
    const response = await fetch(`${base}${path}`, { method: body ? 'POST' : 'GET', headers: getHeaders(apiKey, body ? 'application/json' : ''), body: body ? JSON.stringify(body) : undefined });
    const json = await response.json();
    if (!response.ok) throw new Error(json.detail || 'Model-Lab Fehler');
    return json;
  };

  useEffect(() => { void request('/recommendations').then(setData).catch((error) => setResult(error.message)); }, [apiKey]);

  const act = async (action: 'download' | 'test' | 'rate', model: Model, kind: 'debate' | 'story', rating = 0) => {
    setBusy(`${action}:${model.id}`);
    try {
      const response = await request(`/${action}`, { model_id: model.id, kind, ...(rating ? { rating } : {}) });
      setResult(action === 'test' ? `${response.output}\n\nBewertung: ${JSON.stringify(response.evaluation)}` : JSON.stringify(response));
    } catch (error) { setResult((error as Error).message); }
    finally { setBusy(''); }
  };

  return <div className="md:col-span-2 p-lg bg-bg-card border border-border-subtle rounded-lg">
    <h3 className="font-display text-sm font-semibold mb-sm flex items-center gap-sm"><FlaskConical size={16} /> Tägliches Modelllabor</h3>
    <p className="text-xs text-text-secondary mb-md">Je drei Vorschläge pro Tag. Community-Finetunes sind experimentell und sollten vor Einsatz getestet und bewertet werden.</p>
    {data && (['debate', 'story'] as const).map((kind) => <div key={kind} className="mb-lg">
      <h4 className="text-sm text-accent-cyan mb-sm">{kind === 'debate' ? 'Debatte' : 'Story'} · {data.date}</h4>
      <div className="grid md:grid-cols-3 gap-sm">
        {data[kind].map((model) => <div key={model.id} className="p-md rounded bg-bg-surface border border-border-subtle">
          <div className="text-xs font-semibold text-text-primary break-all">{model.id}</div>
          <div className="text-[10px] text-accent-amber my-xs">{model.size} · {model.tier} · {model.source || 'community'}</div>
          <p className="text-xs text-text-secondary min-h-12">{model.reason}</p>
          <div className="text-[10px] text-text-muted mb-sm">★ {model.rating.average ?? '–'} ({model.rating.count})</div>
          <div className="flex gap-xs flex-wrap">
            <button disabled={!!busy} onClick={() => act('download', model, kind)} className="p-xs text-accent-cyan" title="Download"><Download size={14} /></button>
            <button disabled={!!busy} onClick={() => act('test', model, kind)} className="p-xs text-accent-amber" title="Deutsch testen"><FlaskConical size={14} /></button>
            {[1, 3, 5].map((rating) => <button key={rating} disabled={!!busy} onClick={() => act('rate', model, kind, rating)} className="text-[10px] p-xs text-text-secondary hover:text-accent-cyan"><Star size={11} className="inline" />{rating}</button>)}
          </div>
        </div>)}
      </div>
    </div>)}
    <div className="flex gap-sm mb-md"><Button variant="secondary" onClick={() => request('/setup/debate', {}).then((value) => setResult(JSON.stringify(value))).catch((error) => setResult(error.message))}>Debate-Modell einmalig einrichten</Button><Button variant="secondary" onClick={() => request('/setup/story', {}).then((value) => setResult(JSON.stringify(value))).catch((error) => setResult(error.message))}>Story-Modell einmalig einrichten</Button></div>
    {result && <pre className="p-sm max-h-52 overflow-auto whitespace-pre-wrap text-xs bg-bg-deep text-text-secondary rounded">{result}</pre>}
  </div>;
}
