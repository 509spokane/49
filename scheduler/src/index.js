// GitHub's own cron is often delayed or skipped; workflow_dispatch runs start promptly.
const DISPATCH_URL = 'https://api.github.com/repos/509spokane/49/actions/workflows/webcam-capture.yml/dispatches';

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
};

async function dispatch(env) {
  const res = await fetch(DISPATCH_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': '49-capture-scheduler',
    },
    body: JSON.stringify({ ref: 'main' }),
  });
  if (res.status !== 204) {
    console.log(`Dispatch failed: HTTP ${res.status} ${await res.text()}`);
  }
}
