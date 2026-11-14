"""Advisory controls from the same private read snapshot, never write authority."""
from datetime import timedelta

from ..core import ApiError
from .coaching_contract import instant, stamp
from .reservation_contract import decoded


def eligible(check):
    try:check();return True
    except ApiError:return False


def expiry(c,kind,identifier,at):
    field='hold_id' if kind=='hold' else 'reservation_id'
    occupation='hold' if kind=='hold' else 'request'
    return bool(c.execute('SELECT 1 FROM booking_occupancies WHERE '+field+'=? AND kind=? AND active=1 AND expires_at<=? LIMIT 1',(identifier,occupation,at)).fetchone())


def scheduling_compatible(m,c,row):
    if not c.execute("SELECT 1 FROM users WHERE id=? AND status='active'",(row['member_id'],)).fetchone():return False
    try:_,_,payload,profile=m.coaching.slots.current(c,row['product_id'])
    except ApiError:return False
    original=decoded(row)['product']
    return profile['owner_user_id']==row['owner_id'] and all(payload[k]==original[k] for k in ('durationMinutes','method'))


def hold_controls(m,c,actor,row,at):
    own=bool(actor and actor['id']==row['member_id']);admin=bool(actor and actor['role']=='admin')
    usable=own and eligible(lambda:m.usable_hold(c,actor,row['id'],{'version':row['version']},at))
    parent=m.row(c,'coaching_reservations',row['reservation_id']) if row['reservation_id'] else None
    if parent:
        session=c.execute('SELECT * FROM booking_sessions WHERE reservation_id=? AND ordinal=?',(parent['id'],row['session_ordinal'])).fetchone()
        usable=bool(usable and parent['state']=='confirmed' and parent['valid_until'] and at<parent['valid_until'] and session and (session['state']=='unscheduled' or session['state']=='no_show' and not parent['no_show_consumes']))
    return {'asOf':at,'version':row['version'],'isMember':own,'isOperator':False,'isAdmin':admin,
            'canRelease':bool((own or admin) and row['state']=='active'),'canRequest':bool(usable),
            'canExpire':bool(admin and expiry(c,'hold',row['id'],at))}


def reservation_controls(m,c,actor,row,sessions,at):
    own=bool(actor and actor['id']==row['member_id']);admin=bool(actor and actor['role']=='admin')
    operator=bool(actor and eligible(lambda:m.operator(c,actor,row)))
    # cancel/reschedule treat a coach booking their own product as a member.
    operating=bool(admin or operator and not own)
    policy=decoded(row)['policy'];confirmed=row['state']=='confirmed'
    can_cancel=bool(actor and row['state'] in ('awaiting_coach','awaiting_payment','confirmed','expired','exception') and (own or operating))
    if can_cancel and own and not operating and confirmed:
        cutoff=stamp(instant(at)+timedelta(hours=policy['cancelBeforeHours']))
        for s in sessions:
            if s['state']=='confirmed' and m.row(c,'coaching_slots',s['slot_id'])['starts_at']<=cutoff:can_cancel=False
    valid=bool(confirmed and row['payment_state'] in ('paid','not_required') and row['valid_until'] and at<row['valid_until'] and scheduling_compatible(m,c,row))
    controls=[]
    for s in sessions:
        requested=s['state']=='requested' and row['state'] in ('awaiting_coach','confirmed')
        slot=m.row(c,'coaching_slots',s['slot_id']) if s['slot_id'] else None
        occ=m.row(c,'booking_occupancies',s['occupancy_id']) if s['occupancy_id'] else None
        perform=bool(operator and confirmed and s['state']=='confirmed' and slot and slot['ends_at']<=at)
        move=bool(valid and s['state']=='confirmed' and (operating or own and not policy['rescheduleRequiresCoach']) and occ and occ['occupied_from']>at and slot and slot['starts_at']>stamp(instant(at)+timedelta(hours=policy['rescheduleBeforeHours'])))
        controls.append({'ordinal':s['ordinal'],'version':s['version'],
            'canAccept':bool(operator and requested and eligible(lambda:m.flow.current(c,row,s,at))),
            'canReject':bool(operator and requested),'canComplete':perform,'canNoShow':perform,'canReschedule':move,
            'canSchedule':bool(own and valid and (s['state']=='unscheduled' or s['state']=='no_show' and not row['no_show_consumes']))})
    return {'asOf':at,'version':row['version'],'isMember':own,'isOperator':operator,'isAdmin':admin,
            'canCancel':can_cancel,'cancelCauses':['member_cancel','coach_absence','operator_cancel'] if can_cancel and operating else ['member_cancel'] if can_cancel else [],
            'canExpire':bool(admin and expiry(c,'reservation',row['id'],at)),'sessions':controls}
