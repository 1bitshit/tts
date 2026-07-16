import { tool, type Tool, type ToolsProviderController } from "@lmstudio/sdk";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";

const BASE_URL = process.env.BKG_QWENTTS_URL ?? "http://127.0.0.1:8020";

async function readError(response: Response): Promise<string> {
  const text = await response.text();
  return text || `${response.status} ${response.statusText}`;
}

export async function toolsProvider(ctl: ToolsProviderController) {
  const tools: Tool[] = [];

  tools.push(tool({
    name: "tts_health",
    description: "Check whether the local bkg-qwentts.cpp speech server is ready.",
    parameters: {},
    implementation: async () => {
      for (const path of ["/health", "/v1/health"]) {
        try {
          const response = await fetch(`${BASE_URL}${path}`, { signal: AbortSignal.timeout(3000) });
          if (response.ok) return await response.text();
        } catch {}
      }
      return `Error: bkg-qwentts is unavailable at ${BASE_URL}`;
    },
  }));

  tools.push(tool({
    name: "generate_speech",
    description: "Generate a WAV file with the local bkg-qwentts.cpp server.",
    parameters: {
      text: z.string().min(1),
      voice: z.string().default("default"),
      language: z.string().default("German"),
      file_name: z.string().regex(/^[a-zA-Z0-9._-]+$/).default("speech.wav"),
      seed: z.number().int().optional(),
      temperature: z.number().min(0).max(2).optional(),
    },
    implementation: async ({ text, voice, language, file_name, seed, temperature }) => {
      const response = await fetch(`${BASE_URL}/v1/audio/speech`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          input: text,
          voice,
          language,
          response_format: "wav",
          seed,
          temperature,
        }),
        signal: AbortSignal.timeout(300000),
      });

      if (!response.ok) return `Error: ${await readError(response)}`;

  tools.push(tool({
    name: "generate_speech",
    description: "Generate a WAV file with the local bkg-qwentts.cpp server.",
    parameters: {
      text: z.string().min(1),
      voice: z.string().default("default"),
      language: z.string().default("German"),
      file_name: z.string().regex(/^[a-zA-Z0-9._-]+$/).default("speech.wav"),
      seed: z.number().int().optional(),
      temperature: z.number().min(0).max(2).optional(),
    },
    implementation: async ({ text, voice, language, file_name, seed, temperature }) => {
      const response = await fetch(`${BASE_URL}/v1/audio/speech`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          input: text,
          voice,
          language,
          response_format: "wav",
          seed,
          temperature,
        }),
        signal: AbortSignal.timeout(300000),
      });

      if (!response.ok) return `Error: ${await readError(response)}`;
      const outputDir = join(ctl.getWorkingDirectory(), "bkg-qwentts-audio");
      await mkdir(outputDir, { recursive: true });
      const outputPath = join(outputDir, file_name.endsWith(".wav") ? file_name : `${file_name}.wav`);
      const audio = Buffer.from(await response.arrayBuffer());
      await writeFile(outputPath, audio);

      return JSON.stringify({
        ok: true,
        file: outputPath,
        bytes: audio.byteLength,
        voice,
        language,
      });
    },
  }));

  return tools;
}
