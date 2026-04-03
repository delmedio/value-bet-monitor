import argparse
import json
import sys
import unicodedata
from datetime import datetime, timezone

from scraper import _get


def normalize_name(name: str) -> str:
    text = unicodedata.normalize('NFKD', name or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace('-', ' ').replace('.', ' ')
    return ' '.join(text.split())


def extract_rows(data: dict, bookmaker_name: str = 'SingBet') -> list[dict]:
    bookmakers = data.get('bookmakers', {}) if isinstance(data, dict) else {}
    return bookmakers.get(bookmaker_name, []) or []


def print_market(rows: list[dict], market_name: str) -> None:
    rows_for_market = [m for m in rows if m.get('name') == market_name]
    if not rows_for_market:
        print(f'\n{market_name}: NOT FOUND')
        return

    print(f'\n{market_name}:')
    for m in rows_for_market:
        print(json.dumps({
            'name': m.get('name'),
            'href': m.get('href'),
            'odds': m.get('odds', []),
        }, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--event-id', dest='event_id', default='')
    parser.add_argument('--league-slug', required=False, default='')
    parser.add_argument('--home', required=False, default='')
    parser.add_argument('--away', required=False, default='')
    parser.add_argument('--from-time', dest='from_time', required=False, default='')
    parser.add_argument('--to-time', dest='to_time', required=False, default='')
    args = parser.parse_args()

    event_id = int(args.event_id) if str(args.event_id).strip() else None

    if event_id:
        candidates = [{"id": event_id, "date": None, "home": args.home, "away": args.away}]
        print('\nCANDIDATE EVENTS:')
        print(json.dumps({
            'id': event_id,
            'date': None,
            'home': args.home,
            'away': args.away,
        }, ensure_ascii=False))
    else:
        events = _get('/historical/events', {
            'sport': 'football',
            'league': args.league_slug,
            'from': args.from_time,
            'to': args.to_time,
        })
        if isinstance(events, dict):
            events = events.get('data', events.get('events', []))
        if not isinstance(events, list):
            print('Unexpected events payload:')
            print(json.dumps(events, indent=2, ensure_ascii=False))
            return 1

        target_home = normalize_name(args.home)
        target_away = normalize_name(args.away)
        candidates = []
        for event in events:
            home = event.get('home', '')
            away = event.get('away', '')
            if normalize_name(home) == target_home and normalize_name(away) == target_away:
                candidates.append(event)

        print('\nCANDIDATE EVENTS:')
        if not candidates:
            for event in events[:20]:
                print(json.dumps({
                    'id': event.get('id'),
                    'date': event.get('date'),
                    'home': event.get('home'),
                    'away': event.get('away'),
                }, ensure_ascii=False))
            print('\nNo exact normalized match found.')
            return 2

        for event in candidates:
            print(json.dumps({
                'id': event.get('id'),
                'date': event.get('date'),
                'home': event.get('home'),
                'away': event.get('away'),
            }, ensure_ascii=False))

    for event in candidates:
        event_id = event.get('id')
        print(f'\n=== HISTORICAL ODDS FOR EVENT {event_id} ===')
        data = _get('/historical/odds', {
            'eventId': event_id,
            'bookmakers': 'SingBet',
        })
        rows = extract_rows(data, 'SingBet')
        print(f'Total SingBet markets returned: {len(rows)}')
        print_market(rows, 'Draw No Bet')
        print_market(rows, 'Spread')
        print_market(rows, 'ML')
        print_market(rows, 'Totals')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
