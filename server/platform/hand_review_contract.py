"""Explicit recorded hand facts; validation is not a poker solver."""
import re

from ..core import ApiError, text_field
from .coaching_contract import shape, integer, encode, digest, active, bounded_query

POSITIONS = ('UTG', 'UTG+1', 'UTG+2', 'LJ', 'HJ', 'CO', 'BTN', 'SB', 'BB')
STREETS = ('preflop', 'flop', 'turn', 'river')
GAMES = {'live_cash': '라이브 캐시', 'tournament': '토너먼트', 'online_cash': '온라인 캐시'}
STATES = {'submitted': '접수', 'needs_information': '보완 요청', 'in_review': '검토 중', 'answered': '답변 완료', 'cancelled': '취소'}
LEGACY_STATES = {'접수': 'submitted', '검토 중': 'in_review', '답변 완료': 'answered', '취소': 'cancelled'}


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value):
        raise ApiError(400, '원 요청 키를 확인해주세요.')
    return value


def expected(data, row, field='version'):
    if type(data.get(field)) is not int or data[field] != row['version']:
        raise ApiError(409, '현재 판본과 원 요청 결과를 확인한 뒤 다시 진행해주세요.')


def cards(value, sizes):
    if not isinstance(value, list) or len(value) not in sizes:
        raise ApiError(400, '홀카드와 보드 장수를 확인해주세요.')
    result = []
    for card in value:
        if not isinstance(card, str) or not re.fullmatch(r'[2-9TJQKA][shdcSHDC]', card):
            raise ApiError(400, '카드는 As, Kh처럼 입력해주세요.')
        result.append(card[0] + card[1].lower())
    return result


def hand_payload(value):
    fields = {'title','gameType','effectiveStackMilliBB','heroPosition','heroHand','board','players','actions','question','attachmentIds'}
    shape(value, fields, fields)
    if not isinstance(value['gameType'],str) or value['gameType'] not in GAMES or not isinstance(value['heroPosition'],str) or value['heroPosition'] not in POSITIONS:
        raise ApiError(400, '게임 유형과 히어로 포지션을 확인해주세요.')
    hero = cards(value['heroHand'], (2,)); board = cards(value['board'], (0,3,4,5))
    if len(set(hero + board)) != len(hero + board):
        raise ApiError(400, '같은 카드를 두 번 사용할 수 없습니다.')
    effective = integer(value, 'effectiveStackMilliBB', 1, 20000000)
    players = value['players']
    if not isinstance(players, list) or not 2 <= len(players) <= 9:
        raise ApiError(400, '참여 좌석은 2–9명으로 명시해주세요.')
    normalized = []; remaining = {}
    for player in players:
        shape(player, {'position','stackMilliBB'}, {'position','stackMilliBB'})
        position = player['position']
        if not isinstance(position, str) or position not in POSITIONS or position in remaining:
            raise ApiError(400, '참여 포지션의 중복과 지원 범위를 확인해주세요.')
        stack = integer(player, 'stackMilliBB', 1, 20000000)
        normalized.append({'position': position, 'stackMilliBB': stack}); remaining[position] = stack
    if value['heroPosition'] not in remaining or effective > remaining[value['heroPosition']] or not any(p['position'] != value['heroPosition'] and p['stackMilliBB'] >= effective for p in normalized):
        raise ApiError(400, '히어로와 상대가 사용할 수 있는 유효 스택을 확인해주세요.')
    actions = value['actions']
    if not isinstance(actions, list) or not 1 <= len(actions) <= 200:
        raise ApiError(400, '기록한 액션을 순서대로 최대200개 입력해주세요.')
    result = []; previous = -1; finished = set()
    for item in actions:
        shape(item, {'street','position','action','amountMilliBB','note'}, {'street','position','action','amountMilliBB'})
        street, position, action = item['street'], item['position'], item['action']
        if not isinstance(street, str) or street not in STREETS or not isinstance(position, str) or position not in remaining or not isinstance(action, str) or action not in ('post_blind','post_ante','check','bet','call','raise','fold','all_in'):
            raise ApiError(400, '액션의 스트리트·좌석·종류를 확인해주세요.')
        index = STREETS.index(street)
        if index < previous or len(board) < (0,3,4,5)[index] or position in finished or action.startswith('post_') and index != 0:
            raise ApiError(400, '기록 순서·보드 또는 이미 종료된 좌석의 액션을 확인해주세요.')
        amount = integer(item, 'amountMilliBB', 0 if action in ('check','fold') else 1, 20000000)
        if action in ('check','fold') and amount != 0 or amount > remaining[position] or action == 'all_in' and amount != remaining[position]:
            raise ApiError(400, '액션 금액과 남은 스택을 확인해주세요.')
        remaining[position] -= amount
        if action == 'fold' or remaining[position] == 0: finished.add(position)
        previous = index
        result.append({'street': street,'position': position,'action': action,'amountMilliBB': amount,'note': text_field(item,'note',maximum=500,optional=True)})
    refs = value['attachmentIds']
    if not isinstance(refs, list) or len(refs) > 10 or any(not isinstance(x,str) or not re.fullmatch(r'hrattachment_[a-f0-9]{32}',x) for x in refs) or len(refs) != len(set(refs)):
        raise ApiError(400, '이 리뷰의 첨부 ID를 중복 없이 최대10개 선택해주세요.')
    return {'title': text_field(value,'title',minimum=2,maximum=100),'gameType': value['gameType'],
            'effectiveStackMilliBB': effective,'heroPosition': value['heroPosition'],'heroHand': hero,'board': board,
            'players': normalized,'actions': result,'question': text_field(value,'question',minimum=5,maximum=3000),'attachmentIds': sorted(refs)}


def legacy_snapshot(row):
    return {k: row[k] for k in row.keys()}


def legacy_projection(payload):
    street_labels = dict(zip(STREETS,('프리플랍','플랍','턴','리버')))
    action_labels = {'post_blind':'블라인드','post_ante':'앤티','check':'체크','bet':'베팅','call':'콜','raise':'레이즈','fold':'폴드','all_in':'올인'}
    def action_text(action):
        amount = action['amountMilliBB']
        number = str(amount//1000)+('.'+str(amount%1000).zfill(3).rstrip('0') if amount%1000 else '')
        return street_labels[action['street']]+' · '+action['position']+' · '+action_labels[action['action']]+(' '+number+' BB' if amount else '')+(' · '+action['note'] if action['note'] else '')
    return {'title': payload['title'],'game_type': GAMES[payload['gameType']], 'position': payload['heroPosition'],
            'stack': payload['effectiveStackMilliBB']/1000, 'hero_hand': ' '.join(payload['heroHand']), 'board': ' '.join(payload['board']),
            'actions': '\n'.join(action_text(a) for a in payload['actions']), 'question': payload['question']}
