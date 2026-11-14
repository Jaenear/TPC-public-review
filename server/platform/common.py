import json
from ..core import ApiError, now_iso, new_id
from ..access import for_member, denial


def roles(connection, user):
    if not user:
        return set()
    result = {
        row[0]
        for row in connection.execute(
            "SELECT role FROM user_roles WHERE user_id=?", (user["id"],)
        )
    }
    if user["role"] == "admin":
        result.add("admin")
    return result


def require_role(connection, user, *allowed):
    if not user:
        raise ApiError(401, "로그인 후 이용해주세요.")
    if not roles(connection, user).intersection(set(allowed) | {"admin"}):
        raise ApiError(403, "이 작업을 처리할 권한이 없습니다.")


def can_manage_course(connection, user, course):
    return bool(
        user
        and (
            user["role"] == "admin"
            or (
                course["instructor_user_id"] == user["id"]
                and "coach" in roles(connection, user)
            )
        )
    )


def has_access(connection, user, course_id):
    if not user:
        return False
    return for_member(connection, user["id"], course_id)["canAccess"]


def require_course_access(connection, user, course):
    if not user:
        raise ApiError(401, "로그인 후 이용해주세요.")
    if not can_manage_course(connection, user, course):
        access = for_member(connection, user["id"], course["id"])
        if not access["canAccess"]:
            raise ApiError(403, denial(access))


def audit(connection, user, action, kind, target_id, details=None):
    audit_id = new_id("audit")
    connection.execute(
        "INSERT INTO audit_logs(id,actor_id,action,target_type,target_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
        (
            audit_id,
            user["id"] if user else None,
            action,
            kind,
            target_id,
            json.dumps(details or {}, ensure_ascii=False),
            now_iso(),
        ),
    )
    return audit_id


def notify(connection, user_id, title, body, href, **event):
    if event:
        from ..notification_records import emit

        return emit(connection, user_id, title, body, href, **event)
    if user_id:
        connection.execute(
            "INSERT INTO notifications(id,user_id,title,body,href,created_at) VALUES(?,?,?,?,?,?)",
            (new_id("notice"), user_id, title, body[:1000], href, now_iso()),
        )


def version(data, row):
    if type(data.get("version")) is not int or data["version"] != row["version"]:
        raise ApiError(409, "다른 변경사항이 있습니다. 새로고침 후 다시 수정해주세요.")


def page_args(query):
    try:
        page = int(query.get("page", ["1"])[0])
    except (ValueError, TypeError):
        raise ApiError(400, "페이지 번호를 확인해주세요.")
    if not 1 <= page <= 10000:
        raise ApiError(400, "페이지 번호를 확인해주세요.")
    return page, 20, (page - 1) * 20


def row_dict(row):
    if row is None:
        return None
    names = {
        "user_id": "userId",
        "course_id": "courseId",
        "lesson_id": "lessonId",
        "board_id": "boardId",
        "post_id": "postId",
        "question_id": "questionId",
        "created_at": "createdAt",
        "updated_at": "updatedAt",
        "accepted_answer_id": "acceptedAnswerId",
        "read_at": "readAt",
        "instructor_user_id": "instructorUserId",
        "coach_id": "coachId",
        "sort_order": "sortOrder",
        "video_asset_id": "videoAssetId",
        "section_id": "sectionId",
        "payment_key": "paymentKey",
        "paid_at": "paidAt",
        "provider_status": "providerStatus",
        "order_id": "orderId",
        "refund_reason": "refundReason",
        "target_id": "targetId",
        "target_type": "targetType",
        "actor_id": "actorId",
        "author_id": "authorId",
        "revision_id": "revisionId",
        "base_version": "baseVersion",
        "lesson_version": "lessonVersion",
        "submitted_at": "submittedAt",
        "reviewed_at": "reviewedAt",
    }
    return {names.get(key, key): row[key] for key in row.keys()}
