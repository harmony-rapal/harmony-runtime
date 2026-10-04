'use strict';
let session;
const localBackend = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
const previewPayload = 'Hello from the Harmony browser simulation.\n';
let previewNumber = 0;
function previewProposal() {
  render({status: 'SIMULATION · no file execution', token: String(++previewNumber),
    packet_sha256: 'Illustrative browser proposal · not a runtime packet',
    proposal: {action: 'Simulate creating hello.txt', mok: 'One illustrative file operation',
      player: 'browser-simulation · no worker', authority: 'No filesystem or production authority'}, receipt: null});
  el('message').textContent = 'Interactive browser simulation. No backend, file write or signed approval. Choose APPROVE or REJECT.';
}
async function previewDecision(decision) {
  if (session.receipt) return;
  const approved = decision === 'APPROVE';
  el('approve').disabled = el('reject').disabled = true;
  try {
    const hash = approved ? [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(previewPayload)))].map(b=>b.toString(16).padStart(2,'0')).join('') : null;
    render({...session, status: 'SIMULATION · ' + decision, receipt: {status: 'FINAL · simulated',
      decision, approver: 'You · browser simulation label', execution_identity: 'No worker execution',
      executed: false, simulated_action: approved, exit_status: null, evidence_sha256: hash,
      receipt_sha256: 'Not a runtime receipt', output_text: approved ? previewPayload : null}});
    el('message').textContent = approved ? 'Simulated approval: sample text and its actual browser-computed hash below. No file was created.' : 'Simulated rejection: no execution. Start a new proposal to try APPROVE.';
  } catch {
    el('message').textContent = 'Simulation hash unavailable. No execution occurred. Reload to retry.';
    el('approve').disabled = el('reject').disabled = false;
  }
}
const el = id => document.getElementById(id);
function render(data) {
  session = data;
  el('status').textContent = data.status;
  el('proposal').replaceChildren();
  for (const [key, value] of Object.entries({...data.proposal, packet_sha256: data.packet_sha256})) {
    const label = document.createElement('strong'); label.textContent = key.replaceAll('_', ' ');
    const text = document.createElement('p'); text.textContent = value;
    el('proposal').append(label, text);
  }
  el('approve').disabled = el('reject').disabled = !!data.receipt;
  el('new').hidden = !data.receipt;
  el('result').hidden = !data.receipt;
  el('evidence-summary').replaceChildren();
  if (data.receipt) {
    for (const [label, value] of [
      ['FINAL receipt', data.receipt.status],
      ['Human decision', data.receipt.decision],
      ['Approver', data.receipt.approver],
      ['Execution identity', data.receipt.execution_identity ?? 'None · rejected'],
      ['Player execution', !localBackend ? 'None · browser simulation' : data.receipt.executed ? 'One sandbox file created' : 'No-op'],
      ['Exit status', data.receipt.exit_status ?? 'Not executed'],
      ['Evidence SHA-256', data.receipt.evidence_sha256 ?? 'None · rejected'],
      ['Receipt SHA-256', data.receipt.receipt_sha256],
      ['Output', data.receipt.output_text ?? 'None']
    ]) {
      const term = document.createElement('dt'); term.textContent = label;
      const valueNode = document.createElement('dd'); valueNode.textContent = value;
      el('evidence-summary').append(term, valueNode);
    }
  }
  el('receipt').textContent = data.receipt ? JSON.stringify(data.receipt, null, 2) : '';
  el('message').textContent = data.receipt ? (data.receipt.executed ? 'Executed: one sandbox file. Content and SHA-256 below.' : 'Rejected: no execution, no file, no exit status.') : 'Inspect the scope, then choose. This Baton can be consumed once.';
}
async function request(path, body) {
  el('approve').disabled = el('reject').disabled = el('new').disabled = true;
  try {
    const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {});
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Local demo backend is not available');
    if (!response.ok) throw new Error((await response.json()).error || 'Request failed');
    render(await response.json());
  } catch (error) {
    el('status').textContent = 'Local demo server unavailable';
    el('message').textContent = `Unavailable: ${error.message}. Start locally with python3 -m demo.server, then open http://localhost:8765/demo.html. Static hosting is a visual preview only; no execution occurs here. Reload to reconnect.`;
  } finally { el('new').disabled = false; }
}
el('approve').onclick = () => localBackend ? request('/api/decision', {decision: 'APPROVE', token: session.token}) : previewDecision('APPROVE');
el('reject').onclick = () => localBackend ? request('/api/decision', {decision: 'REJECT', token: session.token}) : previewDecision('REJECT');
el('new').onclick = () => localBackend ? request('/api/proposal', {token: session.token}) : previewProposal();
if (localBackend) request('/api/session');
else previewProposal();
