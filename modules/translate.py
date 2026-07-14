from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import error, request

from modules.io_utils import dump_json, is_valid_json_file, load_json


class Translator:
    def __init__(self, config: dict[str, Any]) -> None:
        translate_config = config.get('translate', {})
        self.enabled = bool(translate_config.get('enabled', True))
        self.base_url = str(translate_config.get('base_url', '')).rstrip('/')
        self.model = str(translate_config.get('model', ''))
        self.batch_size = int(translate_config.get('batch_size', 20))
        self.timeout_seconds = int(translate_config.get('timeout_seconds', 120))
        self.temperature = float(translate_config.get('temperature', 0.2))
        self.min_batch_size_on_retry = int(translate_config.get('min_batch_size_on_retry', 1))
        cache_dir = Path(config.get('paths', {}).get('cache_dir', 'cache')).expanduser().resolve()
        self.glossary_path = Path(translate_config.get('glossary_path', cache_dir / 'glossary.json')).expanduser().resolve()
        self.glossary = self._load_glossary()
        self.system_prompt = str(
            translate_config.get(
                'system_prompt',
                'Translate Japanese ASMR transcript lines into natural Simplified Chinese. '
                'Keep each line aligned 1:1 with the input lines. Return JSON only.',
            )
        )

    def translate_file(self, clean_json_path: Path, translated_json_path: Path, *, overwrite: bool = False) -> Path:
        if is_valid_json_file(translated_json_path) and not overwrite:
            return translated_json_path

        payload = load_json(clean_json_path)
        source_segments = payload.get('segments', [])
        if not self.enabled:
            translated_segments = [self._passthrough_segment(segment) for segment in source_segments]
        else:
            translated_segments = self._translate_segments(source_segments)

        translated_payload: dict[str, Any] = {
            'audio_path': payload.get('audio_path'),
            'source_clean_json': str(clean_json_path),
            'segment_count': len(translated_segments),
            'warnings': payload.get('warnings', []),
            'segments': translated_segments,
        }
        dump_json(translated_json_path, translated_payload)
        return translated_json_path

    def _translate_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.base_url or not self.model:
            raise ValueError('translate.base_url and translate.model must be configured when translation is enabled')

        translated: list[dict[str, Any]] = []
        for batch_start in range(0, len(segments), self.batch_size):
            batch = segments[batch_start:batch_start + self.batch_size]
            translations = self._request_batch_resilient(batch)
            for source_segment, translation_text in zip(batch, translations):
                translated.append(
                    {
                        'start': source_segment['start'],
                        'end': source_segment['end'],
                        'text': source_segment['text'],
                        'translation': translation_text.strip(),
                        'confidence': source_segment.get('confidence'),
                    }
                )
        return translated

    def _request_batch_resilient(self, batch: list[dict[str, Any]]) -> list[str]:
        try:
            return self._request_batch(batch)
        except Exception:
            if len(batch) <= self.min_batch_size_on_retry:
                raise
            midpoint = max(1, len(batch) // 2)
            return self._request_batch_resilient(batch[:midpoint]) + self._request_batch_resilient(batch[midpoint:])

    def _request_batch(self, batch: list[dict[str, Any]]) -> list[str]:
        numbered_lines = [
            {'index': index, 'text': segment['text']}
            for index, segment in enumerate(batch)
        ]
        glossary_note = ''
        if self.glossary:
            glossary_note = ' Use these required terminology mappings: ' + json.dumps(self.glossary, ensure_ascii=False) + '.'
        user_prompt = (
            'Translate the following Japanese transcript lines into Simplified Chinese for subtitle use.' + glossary_note + ' '
            'Return a JSON array and nothing else. Each item must have keys index and translation.\n\n'
            f'Input:\n{json.dumps(numbered_lines, ensure_ascii=False)}'
        )
        body = {
            'model': self.model,
            'temperature': self.temperature,
            'messages': [
                {'role': 'system', 'content': self.system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        }
        response_payload = _post_json(
            f'{self.base_url}/chat/completions',
            body,
            timeout_seconds=self.timeout_seconds,
        )

        content = response_payload['choices'][0]['message']['content']
        decoded = _extract_json_array(content)
        ordered = sorted(decoded, key=lambda item: item['index'])
        expected_indexes = list(range(len(batch)))
        actual_indexes = [int(item.get('index')) for item in ordered]
        if actual_indexes != expected_indexes:
            raise ValueError(
                f'translation batch indexes mismatch: expected {expected_indexes}, got {actual_indexes}'
            )
        translations = [str(item.get('translation', '')).strip() for item in ordered]
        if any(not item for item in translations):
            raise ValueError('translation batch contains empty translation text')
        return translations

    def _load_glossary(self) -> dict[str, str]:
        if not self.glossary_path.exists():
            return {}
        try:
            payload = load_json(self.glossary_path)
        except Exception:
            return {}
        return {str(term['source']): str(term['target']) for term in payload.get('terms', []) if term.get('source') and term.get('target')}
    def _passthrough_segment(self, segment: dict[str, Any]) -> dict[str, Any]:
        return {
            'start': segment['start'],
            'end': segment['end'],
            'text': segment['text'],
            'translation': segment['text'],
            'confidence': segment.get('confidence'),
        }



def _post_json(url: str, payload: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    http_request = request.Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode('utf-8'))
    except error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'translation API returned HTTP {exc.code}: {detail}') from exc
    except error.URLError as exc:
        raise RuntimeError(f'translation API request failed: {exc}') from exc



def _extract_json_array(content: str) -> list[dict[str, Any]]:
    stripped = content.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find('[')
        end = stripped.rfind(']')
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f'model response does not contain a JSON array: {content!r}')
        parsed = json.loads(stripped[start:end + 1])

    if not isinstance(parsed, list):
        raise ValueError(f'expected list response, got: {type(parsed)!r}')
    return parsed
