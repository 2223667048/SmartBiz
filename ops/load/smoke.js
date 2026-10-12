// Low-risk local smoke load test. Targets health only; NO billable model calls.
import http from 'k6/http';
import { check, sleep } from 'k6';
export const options = {
  vus: 5,
  duration: '20s',
  thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<800'] }
};
export default function () {
  const base = __ENV.BASE_URL || 'http://host.docker.internal:8000';
  const r = http.get(`${base}/health`);
  check(r, {'health status 200': res => res.status === 200});
  sleep(0.3);
}
