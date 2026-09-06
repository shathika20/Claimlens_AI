// ClaimLens_AI Client-side Application Logic

const API_BASE = '';

async function fetchAPI(endpoint, options = {}) {
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, options);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Network error' }));
      throw new Error(err.detail || `Request failed with status ${res.status}`);
    }
    return await res.json();
  } catch (error) {
    console.error(`API error on ${endpoint}:`, error);
    throw error;
  }
}

function getStatusBadge(status) {
  const s = (status || 'IN REVIEW').toUpperCase();
  if (s === 'APPROVED') return `<span class="badge badge-approved">✓ APPROVED</span>`;
  if (s === 'REJECTED') return `<span class="badge badge-rejected">✕ REJECTED</span>`;
  if (s === 'REQUEST INFO' || s === 'REQUEST MISSING INFORMATION') return `<span class="badge badge-request-info">⚠ REQUEST INFO</span>`;
  if (s === 'ESCALATED') return `<span class="badge badge-escalated">⚡ ESCALATED</span>`;
  return `<span class="badge" style="background:rgba(148,163,184,0.15);color:#94a3b8;">${s}</span>`;
}

function getRiskBadge(risk) {
  const r = (risk || 'MEDIUM').toUpperCase();
  if (r === 'LOW') return `<span class="badge badge-risk-low">LOW RISK</span>`;
  if (r === 'MEDIUM') return `<span class="badge badge-risk-medium">MEDIUM RISK</span>`;
  return `<span class="badge badge-risk-high">HIGH RISK</span>`;
}

function formatCurrency(amt) {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(amt || 0);
}

function showToast(message, type = 'info') {
  let toast = document.getElementById('toast-container');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'toast-container';
    toast.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:10px;';
    document.body.appendChild(toast);
  }

  const el = document.createElement('div');
  const bg = type === 'success' ? '#10b981' : (type === 'error' ? '#ef4444' : '#3b82f6');
  el.style.cssText = `background:${bg};color:#fff;padding:12px 20px;border-radius:8px;font-weight:600;box-shadow:0 4px 15px rgba(0,0,0,0.3);font-size:0.9rem;transition:all 0.3s;`;
  el.innerText = message;
  toast.appendChild(el);

  setTimeout(() => {
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 300);
  }, 3500);
}
