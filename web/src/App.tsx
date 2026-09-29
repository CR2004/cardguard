import { useEffect, useMemo, useRef, useState } from 'react';
import { useReducedMotion } from 'motion/react';
import { api } from './api/client';
import type { Config } from './api/types';
import type { StripeCardHandle } from './card/StripeCard';
import { HumanReviewPanel } from './components/HumanReviewPanel';
import { Inspector } from './components/Inspector';
import { InvestigationGraph } from './components/InvestigationGraph';
import { MetricsStrip } from './components/MetricsStrip';
import { OperationsDrawer } from './components/OperationsDrawer';
import { PhaseHeader } from './components/PhaseHeader';
import { useGateReveal } from './components/PolicyGate';
import { StepRail } from './components/StepRail';
import { TopBar } from './components/TopBar';
import { TraceTimeline } from './components/TraceTimeline';
import { TransactionPanel, type Inputs } from './components/TransactionPanel';
import { SCENARIOS, type RingStep, type Scenario } from './investigation/scenarios';
import { steps } from './investigation/steps';
import { useInvestigation } from './investigation/useInvestigation';

const first = SCENARIOS[0];
const DEFAULT_INPUTS: Inputs = {
  amountCents: first?.amountCents ?? 2400, buyerCountry: first?.buyerCountry ?? 'US', attack: '', modelAgent: false,
  gift: '', store: '',
};

export function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [scenario, setScenario] = useState<Scenario['id'] | null>(null);
  const [inputs, setInputs] = useState<Inputs>(DEFAULT_INPUTS);
  const [drawer, setDrawer] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [ring, setRing] = useState<RingStep[] | null>(null);
  const stripeCard = useRef<StripeCardHandle>(null);
  const reduced = useReducedMotion() ?? false;
  const inv = useInvestigation(config);
  const { state, view } = inv;
  const reveal = useGateReveal(view.gate, inv.gateKey, reduced);
  const tokenizing = state.status === 'tokenizing';
  // The rail follows the same applied events as the graph; while Stripe tokenizes a new card it starts over.
  const stepList = useMemo(() => steps(tokenizing ? [] : state.events.slice(0, state.applied), {
    tokenizing, paymentMethod: tokenizing ? null : state.ciphertext, failed: state.status === 'error',
    gateRevealing: reveal.evaluating,
  }), [tokenizing, state.events, state.applied, state.ciphertext, state.status, reveal.evaluating]);

  useEffect(() => {
    api.config().then(setConfig).catch((e: Error) => setConfigError(e.message));
  }, []);

  const busy = tokenizing || (state.status === 'running' && !inv.finished && !inv.awaitingHuman);

  const getPaymentMethod = async () => {
    if (!stripeCard.current) throw new Error('Stripe Elements is not ready.');
    return stripeCard.current.tokenize();
  };

  function start(next: Inputs) {
    if (!config) return;
    setNotice(null);
    setRing(null);
    void inv.run({
      amount_cents: next.amountCents, buyer_country: next.buyerCountry, store: next.store || undefined,
      attack: next.attack || null, model_agent: next.modelAgent, gift_message: next.gift,
    }, getPaymentMethod);
  }

  // The ring: the same card (Stripe tokenizes it afresh each time) at every store of this node, one real
  // checkout after another, the way a card-testing ring keeps going. A held checkout stays in the review
  // queue and the ring moves on; a decline or a block ends it. Earlier checkouts are shown at a glance;
  // the last one plays in full.
  async function startRing(next: Inputs) {
    if (!config) return;
    setNotice(null);
    const steps: RingStep[] = [];
    setRing(steps);
    const stores = config.stores;
    for (const [i, store] of stores.entries()) {
      const result = await inv.run({
        amount_cents: next.amountCents, buyer_country: next.buyerCountry, store,
        attack: null, model_agent: false, gift_message: '',
      }, getPaymentMethod);
      const outcome = result?.outcome ?? 'error';
      steps.push({ store, outcome, band: result?.verdict?.network?.band ?? null });
      const stop = !result || !(outcome === 'approved' || outcome === 'needs_review');
      if (stop || i === stores.length - 1) {
        for (const rest of stores.slice(i + 1)) steps.push({ store: rest, outcome: 'not_reached', band: null });
        setRing([...steps]);
        return;
      }
      setRing([...steps]);
      inv.skip();
    }
  }

  function onScenario(s: Scenario) {
    const next: Inputs = { ...inputs, amountCents: s.amountCents, buyerCountry: s.buyerCountry, attack: s.attack,
      modelAgent: s.modelAgent, gift: s.gift || inputs.gift };
    setScenario(s.id);
    setInputs(next);
    if (s.ring && config && config.stores.length < 3) {
      setNotice('This scenario needs three stores on the node: start it with python run_demo.py --stores store-a,store-b,store-c.');
      return;
    }
    // The card is typed into Stripe's own field; once it is complete a scenario runs in one click.
    if (!stripeCard.current?.isComplete()) setNotice(`Type the ${s.testCard} into the Stripe field, then press Pay.`);
    else if (s.ring) void startRing(next);
    else start(next);
  }

  if (!config) {
    return (
      <div className="app app--boot">
        <p className={configError ? 'error-note' : 'boot-note'} role={configError ? 'alert' : 'status'}>
          {configError ? `The store node did not answer (${configError}). Start it with python run_demo.py.` : 'Connecting to the store node…'}
        </p>
      </div>
    );
  }

  const lastT = state.events.at(-1)?.t;
  const status = tokenizing ? { tone: 'live' as const, text: 'Tokenizing' }
    : state.status === 'error' ? { tone: 'error' as const, text: 'Not started' }
      : inv.awaitingHuman && state.applied >= state.events.length ? { tone: 'review' as const, text: 'Awaiting a reviewer' }
        : state.status === 'running' && (inv.live || !inv.finished) ? { tone: 'live' as const, text: 'Investigating' }
          : state.status === 'running' ? { tone: 'done' as const, text: 'Complete' }
            : { tone: 'idle' as const, text: 'Ready' };
  const showReview = Boolean(view.review && !view.review.decided);

  return (
    <div className="app">
      <TopBar
        config={config}
        status={status}
        elapsedMs={lastT}
        canReplay={state.events.length > 0 && state.applied >= state.events.length}
        canSkip={state.applied < state.events.length}
        onReplay={inv.replay}
        onSkip={inv.skip}
        onOperations={() => setDrawer(true)}
      />
      <div className="workspace">
        <TransactionPanel
          config={config}
          scenario={scenario}
          inputs={inputs}
          onScenario={onScenario}
          onInputs={(patch) => setInputs((cur) => ({ ...cur, ...patch }))}
          onRun={() => (scenario === 'ring' ? void startRing(inputs) : start(inputs))}
          busy={busy}
          after={state.traceId !== null}
          tokenizing={tokenizing}
          received={state.ciphertext}
          error={state.error ?? notice}
          ring={ring}
          stripeCard={stripeCard}
        />
        <section className="band" aria-label="Investigation steps">
          <StepRail steps={stepList} />
        </section>
        <main className="stage-wrap" aria-label="Investigation graph">
          <div className="stage-head">
            <PhaseHeader view={view} tokenizing={tokenizing} hint={SCENARIOS.find((s) => s.id === scenario)} />
          </div>
          <InvestigationGraph view={view} prev={inv.prevView} reduced={reduced} gateKey={inv.gateKey} reveal={reveal}
            buyerCountry={state.traceId ? inputs.buyerCountry : undefined} />
          <div className="stage-foot">
            <MetricsStrip metrics={view.metrics} flower={view.mode === 'flower'} />
          </div>
        </main>
        {showReview
          ? <HumanReviewPanel view={view} onDecide={inv.decide} />
          : <Inspector view={view} reveal={reveal} gateKey={inv.gateKey} reduced={reduced} />}
      </div>
      <TraceTimeline events={state.events} applied={state.applied} />
      {drawer && <OperationsDrawer onClose={() => setDrawer(false)} refreshKey={state.runs} />}
    </div>
  );
}
