document.getElementById('demoState').addEventListener('change', event => {
 document.querySelectorAll('.case-card').forEach(card => { card.hidden = Boolean(event.target.value && card.dataset.state !== event.target.value); });
});
document.querySelectorAll('[data-track]').forEach(button => {
 button.addEventListener('click', async () => {
 const panel = button.closest('.case-card').querySelector('.live-progress');
 button.disabled = true;
 panel.textContent = 'Loading current progress...';
 try {
 const response = await fetch(`/api/v1/requests/${encodeURIComponent(button.dataset.track)}/track`, { cache: 'no-store' });
 const data = await response.json();
 if (!response.ok || !data.success) throw new Error(data.error || 'Unable to load this ticket.');
 panel.replaceChildren();
 const heading = document.createElement('strong');
 heading.textContent = `Stage ${data.current_stage_index} of 5 · ${data.status}`;
 panel.append(heading);
 const list = document.createElement('ol');
 data.timeline_events.forEach(event => {
 const item = document.createElement('li');
 item.textContent = `${event.to_status}: ${event.reason || 'Status recorded.'}`;
 const date = document.createElement('small');
 date.textContent = `${event.created_at.slice(0, 16).replace('T', ' ')} UTC · ${event.actor}`;
 item.append(date);
 list.append(item);
 });
 panel.append(list);
 } catch (error) { panel.textContent = error.message; }
 finally { button.disabled = false; }
 });
});
