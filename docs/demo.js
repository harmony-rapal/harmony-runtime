'use strict';
let session;
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
      ['Player execution', data.receipt.executed ? 'One sandbox file created' : 'No-op'],
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
    if (!response.ok) throw new Error((await response.json()).error || 'Request failed');
    render(await response.json());
  } catch (error) {
    el('status').textContent = 'OFFLINE · visual preview only';
    el('message').textContent = `Unavailable: ${error.message}. Start locally with python3 -m demo.server, then open http://localhost:8765/demo.html. Static hosting is a visual preview only; no execution occurs here. Reload to reconnect.`;
  } finally { el('new').disabled = false; }
}
el('approve').onclick = () => request('/api/decision', {decision: 'APPROVE', token: session.token});
el('reject').onclick = () => request('/api/decision', {decision: 'REJECT', token: session.token});
el('new').onclick = () => request('/api/proposal', {token: session.token});
request('/api/session');
