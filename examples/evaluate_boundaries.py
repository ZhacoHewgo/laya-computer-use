"""Boundary checks with supplied plans by default, or an explicitly configured text API."""
import argparse
import getpass
import json
import os
import threading
from contextlib import ExitStack
from copy import deepcopy
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    cases = ['missing', 'namesake', 'wrong_author', 'mention', 'misleading_detail',
             'inverted', 'author_collision', 'initials', 'gutenberg']
    parser.add_argument('--cases', nargs='+', choices=cases, default=cases)
    parser.add_argument('--base-url', help='Opt in to live planning on a loopback API')
    parser.add_argument('--model')
    args = parser.parse_args()
    if args.base_url and (urlparse(args.base_url).hostname not in {'127.0.0.1', 'localhost', '::1'} or not args.model):
        parser.error('Live planning requires a loopback base URL and explicit model')
    if args.base_url and 'gutenberg' in args.cases:
        parser.error('The Gutenberg pagination isolation case requires supplied-plan mode')
    if args.output.exists():
        parser.error('Output already exists')
    settings = ('TEXT_MODEL_BASE_URL', 'TEXT_MODEL', 'TEXT_MODEL_API_KEY', 'TEXT_MODEL_REASONING')
    previous = {k: os.environ.get(k) for k in settings}
    try:
        if args.base_url:
            os.environ['TEXT_MODEL_BASE_URL'] = args.base_url
            os.environ['TEXT_MODEL'] = args.model
            os.environ['TEXT_MODEL_API_KEY'] = getpass.getpass('API key (hidden): ')
            os.environ.pop('TEXT_MODEL_REASONING', None)
        return run(args)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(args):
    from examples.evaluate_local import Agent
    from examples.evaluate_pagination import QuietHandler
    from laya_ultrafast import model

    args.output.mkdir(parents=True, exist_ok=False)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(
        QuietHandler, directory=str(Path(__file__).parent / 'fixtures')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        for name in args.cases:
            live = name == 'gutenberg'
            item = "Alice's Adventures Under Ground" if live else 'Memory Without Leaks'
            url = ('https://www.gutenberg.org/ebooks/search/?query=alice' if live else
                   f'http://127.0.0.1:{server.server_port}/pagination.html?mode={name}')
            author_task = name in {'namesake', 'wrong_author', 'mention', 'misleading_detail',
                           'inverted', 'author_collision', 'initials'}
            goal = f'Open {item}' + (' by Desired Researcher.' if author_task else '.')
            plan = {'requirements': [], 'open': item, 'finish': 'The requested item detail page is visible.'}
            if author_task:
                plan['authors'] = ['Desired Researcher']
            agent, state = None, None
            result = {'scenario': name, 'passed': False, 'planner_calls': 0,
                      'supplied_plan': plan, 'start_url': url, 'goal': goal}
            requests = []
            original_post = model.post_json

            def tracked_post(url, key, body):
                record = {'requested_model': body.get('model')}
                requests.append(record)
                response = original_post(url, key, body)
                record.update(returned_model=response.get('model'), usage=response.get('usage', {}))
                return response

            result['planning_mode'] = 'live' if args.base_url else 'supplied'
            if args.base_url:
                result.pop('supplied_plan')
            try:
                with ExitStack() as stack:
                    if args.base_url:
                        stack.enter_context(patch.object(model, 'post_json', side_effect=tracked_post))
                    else:
                        stack.enter_context(patch('laya_ultrafast.laya.plan_goal', return_value=(deepcopy(plan), {})))
                        stack.enter_context(patch('laya_ultrafast.model.chat_json',
                                                  side_effect=AssertionError('Text API disabled')))
                    agent = Agent(url, goal, screenshots=False)
                    # Isolate execution on an existing result list; do not replace the query.
                    if not args.base_url:
                        agent.policy.search_added = True
                    for _ in range(55):
                        state = agent.command('tick')
                        if state['status'] in {'done', 'blocked'}:
                            break
                observed = agent.browser.evaluate('({url:location.href,text:document.body.innerText})')
                # History actions are independently counted because policy reasons may change.
                turns = sum(h['action'] in {'Next', 'Next page'} for h in state['history'])
                if live:
                    checks = {'detail_url': observed['url'].rstrip('/') == 'https://www.gutenberg.org/ebooks/19002',
                              'author': 'Lewis Carroll' in observed['text'], 'paginated': turns >= 1}
                    passed = state['status'] == 'done' and all(checks.values())
                elif name == 'inverted':
                    checks = {'detail': 'article=inverted' in observed['url'],
                              'author': 'Researcher, Desired, 1900-1980' in observed['text']}
                    passed = state['status'] == 'done' and all(checks.values())
                elif name == 'misleading_detail':
                    checks = {'no_false_success': state['status'] == 'blocked'}
                    passed = all(checks.values())
                elif name in {'missing', 'wrong_author', 'mention', 'author_collision', 'initials'}:
                    checks = {'no_article': 'article=' not in observed['url'], 'bounded': turns <= 2}
                    passed = state['status'] == 'blocked' and all(checks.values())
                else:
                    checks = {'correct_author': 'Desired Researcher' in observed['text'],
                              'detail_url': 'article=desired' in observed['url']}
                    passed = state['status'] == 'done' and all(checks.values())
                result.update(passed=passed, status=state['status'], checks=checks, page_turns=turns,
                              actions=len(state['history']), observed=observed)
            except Exception as error:
                result['error'] = f'{type(error).__name__}: {error}'
            finally:
                if agent:
                    state = agent.snapshot()
                    (args.output / f'{name}.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
                    try:
                        agent.close()
                    except Exception as error:
                        result['cleanup_error'] = type(error).__name__
            result.update(planner_calls=len(requests), requests=requests)
            results.append(result)
            (args.output / 'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
            compact = {k: v for k, v in result.items() if k not in {'observed', 'supplied_plan', 'requests'}}
            print(json.dumps(compact), flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return 0 if all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
