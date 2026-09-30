import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { TraceEvent } from '../api/types';
import { derive } from '../investigation/derive';
import flower from '../investigation/fixtures/flower-collaborative.json';
import { HumanReviewPanel } from './HumanReviewPanel';

// A real recorded trace that ends held for a person.
const view = derive(flower as TraceEvent[], 'flower');
const render = (demo: boolean) => renderToStaticMarkup(createElement(HumanReviewPanel, { view, demo, onDecide: async () => ({}) }));
const buttons = (html: string) => [...html.matchAll(/<button[^>]*class="review-btn[^"]*"[^>]*>/g)].map((m) => m[0]);

describe('HumanReviewPanel', () => {
  it('is held for a person in the fixture', () => {
    expect(view.review?.id).toBeTruthy();
    expect(view.review?.decided).toBeUndefined();
  });

  it('demo controls: only Approve and Decline, no credential field, both enabled', () => {
    const html = render(true);
    expect(html).toContain('Human review required');
    expect(html).not.toContain('id="reviewer"');
    expect(html).not.toContain('type="password"');
    expect(buttons(html)).toHaveLength(2);
    for (const b of buttons(html)) expect(b).not.toContain('disabled');
  });

  it('without demo controls: the credential field is asked for and the buttons wait for it', () => {
    const html = render(false);
    expect(html).toContain('id="reviewer"');
    for (const b of buttons(html)) expect(b).toContain('disabled');
  });
});
