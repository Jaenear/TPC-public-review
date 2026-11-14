"""New business API routing; legacy learning APIs remain compatible."""

import re
from ..core import ApiError, now_iso, text_field
from .common import require_course_access, require_role, row_dict
from .content import ContentService
from .accounts import AccountService
from .discussions import DiscussionService
from .commerce import CommerceService
from .revisions import RevisionService
from .policies import PolicyService
from .profiles import ProfileService
from .contracts import ContractService
from .pricing import PricingService
from .curriculum import CurriculumService
from .resources import ResourceService
from .publications import PublicationService
from .media_uploads import MediaUploadService
from .learning_start import LearningStartService
from .watch_progress import WatchProgressService
from .lesson_learning import LessonLearningService
from .captions import CaptionService
from .discussion_api import DiscussionAPI
from .discussion_attachments import DiscussionAttachments
from .moderation import ModerationService
from .notifications import NotificationService
from .community_comments import CommunityCommentService
from .community_blocks import CommunityBlockService
from .refund_context import RefundContextService
from .refund_workflow import RefundWorkflowService
from .reconciliation import ReconciliationService
from .reconciliation_runtime import ReconciliationRuntime
from .reconciliation_desk import ReconciliationDesk
from .coaching_products import CoachingService
from .reservation_model import ReservationModel
from .reservation_api import ReservationAPI
from .hand_reviews import HandReviews
from .hand_review_api import HandReviewAPI
from .mail_model import MailService
from .mail_api import MailContractAPI
from .identity_model import IdentityService
from .identity_mail import IdentityMailSource
from .identity_api import IdentityContractAPI
from .identity_recovery import IdentityRecovery
from .identity_commands_api import IdentityCommandsAPI

HANDLED = object()


class Platform:
    def __init__(self, app, gateway=None, identity_keyring=None):
        self.app = app
        self.content = ContentService(app)
        self.accounts = AccountService(app)
        self.discussions = DiscussionService(app)
        self.community_comments = CommunityCommentService(app, self.discussions)
        self.community_blocks = CommunityBlockService(app)
        self.refund_context = RefundContextService(app)
        self.policies = PolicyService(app)
        self.profiles = ProfileService(app)
        self.contracts = ContractService(app)
        self.pricing = PricingService(app)
        self.commerce = CommerceService(app, gateway)
        self.refund_workflow = RefundWorkflowService(app, self.commerce)
        self.reconciliation = ReconciliationService(app)
        self.reconciliation_runtime = ReconciliationRuntime(app, self.commerce, self.reconciliation)
        self.reconciliation_desk = ReconciliationDesk(app, self.reconciliation_runtime)
        self.coaching = CoachingService(app)
        self.reservations = ReservationModel(app, self.coaching)
        self.reservation_api = ReservationAPI(app, self.reservations)
        self.hand_reviews = HandReviews(app)
        self.hand_review_api = HandReviewAPI(app, self.hand_reviews)
        self.identity = IdentityService(app, identity_keyring)
        self.mail = MailService(app, source=IdentityMailSource(self.identity))
        self.identity.mail = self.mail
        self.mail_api = MailContractAPI(self.mail)
        self.identity_api = IdentityContractAPI(self.identity)
        self.identity_recovery = IdentityRecovery(app, self.identity)
        self.identity_commands_api = IdentityCommandsAPI(self.identity, self.identity_recovery)
        self.revisions = RevisionService(app, self.content)
        self.curriculum = CurriculumService(app)
        self.resources = ResourceService(app)
        self.publications = PublicationService(app)
        self.media_uploads = MediaUploadService(app)
        self.learning_start = LearningStartService(app)
        self.watch_progress = WatchProgressService(app)
        self.lesson_learning = LessonLearningService(app)
        self.captions = CaptionService(app)
        self.discussion_attachments = DiscussionAttachments(app, self.discussions)
        self.discussion_api = DiscussionAPI(app, self.discussions)
        self.moderation = ModerationService(app, self.discussions)
        self.notifications = NotificationService(app)

    def route(self, h, path, method, query):
        user = h.user
        result = None
        for service in (self.community_comments, self.community_blocks, self.refund_context, self.refund_workflow, self.reconciliation_runtime, self.reconciliation_desk, self.reservation_api, self.hand_review_api, self.mail_api, self.identity_commands_api, self.identity_api, self.coaching):
            community_result = service.route(h, path, method, query)
            if community_result is not None:
                return community_result
        notification_result = self.notifications.route(h, path, method, query)
        if notification_result is not None:
            return notification_result
        moderation_result = self.moderation.route(h, path, method, query)
        if moderation_result is not None:
            return moderation_result
        match = re.fullmatch(
            r"/api/qa/(questions|answers)/([a-z0-9_]+)/revisions", path
        )
        if match:
            if method != "GET":
                raise ApiError(405, "수정 이력은 조회로만 제공됩니다.")
            kind = "question" if match.group(1) == "questions" else "answer"
            return (
                200,
                self.discussion_api.revisions(user, kind, match.group(2), query),
                {},
            )
        match = re.fullmatch(r"/api/qa/answers/([a-z0-9_]+)", path)
        if match:
            if method != "PATCH":
                raise ApiError(405, "답변 본문은 명시적으로 수정해주세요.")
            return (
                200,
                self.discussion_api.edit(user, "answer", match.group(1), h.body()),
                {},
            )
        match = re.fullmatch(r"/api/qa/questions/([a-z0-9_]+)/attachments", path)
        if match:
            if method == "GET":
                return (
                    200,
                    self.discussion_attachments.listing(user, match.group(1)),
                    {},
                )
            if method == "POST":
                return (
                    200,
                    self.discussion_attachments.upload(h, user, match.group(1)),
                    {},
                )
            raise ApiError(405, "지원하지 않는 첨부 요청입니다.")
        match = re.fullmatch(
            r"/api/qa/attachments/(qaattachment_[a-f0-9]{32})/(file|inspect|cancel|block|delete)",
            path,
        )
        if match:
            aid, action = match.groups()
            if action == "file" and method == "GET":
                self.discussion_attachments.file(h, user, aid)
                return HANDLED
            if action != "file" and method == "POST":
                return (
                    200,
                    self.discussion_attachments.operate(user, aid, action, h.body()),
                    {},
                )
            raise ApiError(405, "지원하지 않는 첨부 처리입니다.")
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/media/([a-z0-9_]+)/captions", path
        )
        if match:
            cid, aid = match.groups()
            if method == "GET":
                return 200, self.captions.listing(user, cid, aid, query), {}
            if method == "POST":
                return 200, self.captions.upload(h, user, cid, aid), {}
            raise ApiError(405, "지원하지 않는 자막 요청입니다.")
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/captions/(caption_[a-z0-9]+)(?:/(file|review))?",
            path,
        )
        if match:
            cid, rid, action = match.groups()
            if action == "file" and method == "GET":
                self.captions.file(h, user, cid, rid)
                return HANDLED
            if action == "review" and method == "POST":
                return 200, self.captions.review(user, cid, rid, h.body()), {}
            if action is None and method == "GET":
                return 200, self.captions.detail(user, cid, rid), {}
            raise ApiError(405, "지원하지 않는 자막 검수 요청입니다.")
        match = re.fullmatch(
            r"/api/courses/([a-z0-9-]+)/lessons/([a-z0-9-]+)/captions(?:/(caption_[a-z0-9]+))?",
            path,
        )
        if match:
            cid, lid, rid = match.groups()
            if method != "GET":
                raise ApiError(405, "자막은 조회로만 제공됩니다.")
            if rid:
                self.captions.file(h, user, cid, rid, lid)
                return HANDLED
            return 200, self.captions.learner_read(user, cid, lid), {}
        learning_match = re.fullmatch(
            r"/api/courses/([a-z0-9-]+)/lessons/([a-z0-9-]+)/(learning|quiz-attempts|completion-events)",
            path,
        )
        if learning_match:
            cid, lid, resource = learning_match.groups()
            if resource == "learning" and method == "GET":
                result = self.lesson_learning.read(user, cid, lid)
            elif resource == "quiz-attempts" and method == "POST":
                result = self.lesson_learning.write(user, cid, lid, h.body(), "quiz")
            elif resource in ("quiz-attempts", "completion-events") and method == "GET":
                result = self.lesson_learning.history(
                    user,
                    cid,
                    lid,
                    query,
                    "quiz" if resource == "quiz-attempts" else "completion",
                )
            else:
                raise ApiError(405, "지원하지 않는 학습 기록 요청입니다.")
            return 200, result, {}
        start_match = re.fullmatch(r"/api/courses/([a-z0-9-]+)/start", path)
        if start_match:
            if method == "GET":
                result = self.learning_start.read(user, start_match.group(1))
            elif method == "POST":
                result = self.learning_start.acknowledge(
                    user, start_match.group(1), h.body()
                )
            else:
                raise ApiError(405, "지원하지 않는 학습 시작 요청입니다.")
            return 200, result, {}
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/media-uploads(?:/(upload_[a-z0-9]{32})(/chunks)?)?",
            path,
        )
        if match:
            cid, uid, chunks = match.groups()
            if chunks:
                if method != "PATCH":
                    raise ApiError(405, "영상 조각은 PATCH로 전송해주세요.")
                data = self.media_uploads.put(h, user, cid, uid)
            elif uid:
                if method == "GET":
                    data = self.media_uploads.detail(user, cid, uid)
                elif method == "POST":
                    body = h.body()
                    action = body.get("action")
                    if action == "complete":
                        data = self.media_uploads.complete(user, cid, uid, body)
                    elif action == "cancel":
                        data = self.media_uploads.cancel(user, cid, uid, body)
                    else:
                        raise ApiError(400, "업로드 완료/취소 작업을 확인해주세요.")
                else:
                    raise ApiError(405, "지원하지 않는 영상 업로드 요청입니다.")
            elif method == "GET":
                data = self.media_uploads.listing(user, cid, query)
            elif method == "POST":
                data = self.media_uploads.new(user, cid, h.body())
            else:
                raise ApiError(405, "지원하지 않는 영상 업로드 요청입니다.")
            return 200, data, {}
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/media-uploads/cleanup", path
        )
        if match and method == "POST":
            return 200, self.media_uploads.cleanup(user, match.group(1), h.body()), {}
        if path == "/api/studio/course-reviews" and method == "GET":
            return 200, self.publications.queue(user, query), {}
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/publication(?:/reviews(?:/(coursereview_[a-z0-9]+)(/comments)?)?)?",
            path,
        )
        if match:
            cid, rid, comments = match.groups()
            if comments and method == "POST":
                data = self.publications.comment(user, cid, rid, h.body())
            elif method == "GET":
                data = (
                    self.publications.exact(user, cid, rid, query)
                    if rid
                    else self.publications.listing(user, cid, query)
                )
            elif method == "POST" and path.endswith("/reviews"):
                data = self.publications.new(user, cid)
            elif rid and method in ("PATCH", "POST") and not comments:
                data = self.publications.change(
                    user, cid, rid, h.body(), save=method == "PATCH"
                )
            else:
                raise ApiError(405, "지원하지 않는 강의 검수 요청입니다.")
            return 200, data, {}
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/curriculum(?:/changes(?:/(curriculum_[a-z0-9]+))?)?",
            path,
        )
        if match:
            cid, rid = match.groups()
            if method == "GET":
                data = (
                    self.curriculum.exact(user, cid, rid)
                    if rid
                    else self.curriculum.listing(user, cid, query)
                )
            elif method == "POST" and path.endswith("/changes"):
                data = self.curriculum.new(user, cid)
            elif method in ("POST", "PATCH") and rid:
                data = self.curriculum.change(
                    user, cid, rid, h.body(), save=method == "PATCH"
                )
            else:
                raise ApiError(405, "지원하지 않는 커리큘럼 요청입니다.")
            return 200, data, {}
        match = re.fullmatch(r"/api/studio/courses/([a-z0-9-]+)/lessons/batch", path)
        if match and method == "POST":
            return 200, self.curriculum.batch(user, match.group(1), h.body()), {}
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/resources(?:/(resource_[a-z0-9]+)(/download)?)?",
            path,
        )
        if match:
            cid, rid, download = match.groups()
            if download and method == "GET":
                self.resources.download(h, user, cid, rid)
                return HANDLED
            if method == "GET":
                data = (
                    self.resources.exact(user, cid, rid)
                    if rid
                    else self.resources.listing(user, cid, query)
                )
            elif method == "POST":
                data = (
                    self.resources.review(user, cid, rid, h.body())
                    if rid
                    else self.resources.upload(h, user, cid)
                )
            else:
                raise ApiError(405, "지원하지 않는 자료 요청입니다.")
            return 200, data, {}
        match = re.fullmatch(
            r"/api/courses/([a-z0-9-]+)/lessons/([a-z0-9-]+)/resources/(resource_[a-z0-9]+)",
            path,
        )
        if match and method == "GET":
            cid, lid, rid = match.groups()
            self.resources.download(h, user, cid, rid, lid)
            return HANDLED
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/prices(?:/(price_[a-z0-9]+))?", path
        )
        if match:
            cid, pid = match.groups()
            if method == "GET":
                result = (
                    self.pricing.exact(user, cid, pid)
                    if pid
                    else self.pricing.listing(user, cid, query)
                )
            elif method == "POST":
                result = (
                    self.pricing.change(user, cid, pid, h.body())
                    if pid
                    else self.pricing.new(user, cid, h.body())
                )
            elif method == "PATCH" and pid:
                result = self.pricing.change(user, cid, pid, h.body(), save=True)
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/sale-(control|readiness)", path
        )
        if match:
            if method == "POST" and match.group(2) == "control":
                result = self.pricing.control(user, match.group(1), h.body())
            elif method == "GET" and match.group(2) == "readiness":
                result = self.pricing.readiness(user, match.group(1), query)
        match = re.fullmatch(r"/api/checkout/orders/([a-z0-9_]+)/price", path)
        if match and method == "GET":
            result = self.pricing.order_snapshot(user, match.group(1))
        if path == "/api/manage/contract-partners" and method == "GET":
            result = self.contracts.partners(user, query)
        if path == "/api/studio/contracts" and method == "GET":
            result = self.contracts.listing(user, query)
        if path == "/api/manage/contracts" and method == "POST":
            result = self.contracts.create(user, h.body())
        match = re.fullmatch(r"/api/studio/contracts/(contract_[a-z0-9]+)", path)
        if match and method == "GET":
            result = self.contracts.detail(user, match.group(1), query)
        match = re.fullmatch(
            r"/api/studio/contracts/(contract_[a-z0-9]+)/bindings", path
        )
        if match and method == "GET":
            result = self.contracts.bindings(user, match.group(1), query)
        match = re.fullmatch(r"/api/studio/contracts/(contract_[a-z0-9]+)/orders", path)
        if match and method == "GET":
            result = self.contracts.orders(user, match.group(1), query)
        match = re.fullmatch(
            r"/api/manage/contracts/(contract_[a-z0-9]+)/versions", path
        )
        if match and method == "POST":
            result = self.contracts.new_version(user, match.group(1))
        match = re.fullmatch(
            r"/api/(manage|studio)/contracts/(contract_[a-z0-9]+)/versions/(contractversion_[a-z0-9]+)",
            path,
        )
        if match:
            if method == "GET":
                result = self.contracts.exact(user, match.group(2), match.group(3))
            elif method in ("PATCH", "POST") and match.group(1) == "manage":
                result = self.contracts.change(
                    user,
                    match.group(2),
                    match.group(3),
                    h.body(),
                    save=method == "PATCH",
                )
        match = re.fullmatch(
            r"/api/manage/contracts/(contract_[a-z0-9]+)/versions/(contractversion_[a-z0-9]+)/bindings",
            path,
        )
        if match and method == "POST":
            result = self.contracts.bind(user, *match.groups(), h.body())
        match = re.fullmatch(
            r"/api/manage/contract-bindings/(productcontract_[a-z0-9]+)/stop", path
        )
        if match and method == "POST":
            result = self.contracts.stop(user, match.group(1), h.body())
        match = re.fullmatch(
            r"/api/(?:studio/contract-orders|manage/purchases)/([a-z0-9_]+)(?:/contract)?",
            path,
        )
        if (
            match
            and method == "GET"
            and (
                path.startswith("/api/studio/contract-orders/")
                or path.endswith("/contract")
            )
        ):
            result = self.contracts.order_snapshot(
                user, match.group(1), admin=path.startswith("/api/manage/")
            )
        if path == "/api/manage/profile-owners" and method == "GET":
            result = self.profiles.owners(user, query)
        match = re.fullmatch(r"/api/bookings/([a-z0-9_]+)/profile", path)
        if match and method == "GET":
            result = self.profiles.booking_snapshot(user, match.group(1))
        match = re.fullmatch(r"/api/coaches/([a-z0-9-]+)", path)
        if match and method == "GET":
            result = self.profiles.public(match.group(1))
        if path == "/api/studio/profiles" and method == "GET":
            result = self.profiles.listing(user, query)
        if path == "/api/manage/profiles" and method == "POST":
            result = self.profiles.create(user, h.body())
        match = re.fullmatch(r"/api/studio/profiles/([a-z0-9-]+)", path)
        if match and method == "GET":
            result = self.profiles.detail(user, match.group(1))
        match = re.fullmatch(r"/api/manage/profiles/([a-z0-9-]+)", path)
        if match and method == "PATCH":
            result = self.profiles.manage(user, match.group(1), h.body())
        match = re.fullmatch(r"/api/studio/profiles/([a-z0-9-]+)/revisions", path)
        if match:
            if method == "POST":
                result = self.profiles.new_revision(user, match.group(1), h.body())
            elif method == "GET":
                result = self.profiles.history(user, match.group(1), query)
        match = re.fullmatch(
            r"/api/studio/profiles/([a-z0-9-]+)/revisions/(profilerevision_[a-z0-9]+)",
            path,
        )
        if match and method in ("PATCH", "POST"):
            result = self.profiles.change_revision(
                user, *match.groups(), h.body(), save=method == "PATCH"
            )
        match = re.fullmatch(
            r"/api/studio/profiles/([a-z0-9-]+)/revisions/([a-z0-9_]+)", path
        )
        if match and method == "GET":
            result = self.profiles.get_revision(user, *match.groups())
        match = re.fullmatch(
            r"/api/manage/profiles/([a-z0-9-]+)/verifications(?:/(coachfact_[a-z0-9]+))?",
            path,
        )
        if match and method == "POST":
            result = self.profiles.verification(
                user, match.group(1), h.body(), match.group(2)
            )
        match = re.fullmatch(r"/api/studio/profiles/([a-z0-9-]+)/bookings", path)
        if match and method == "GET":
            result = self.profiles.inquiries(user, match.group(1), query)
        match = re.fullmatch(r"/api/manage/bookings/([a-z0-9_]+)/handoff", path)
        if match:
            if method == "GET":
                result = self.profiles.handoff_history(user, match.group(1))
            elif method == "POST":
                result = self.profiles.handoff(user, match.group(1), h.body())
        if path == "/api/policies/current" and method == "GET":
            result = self.policies.current(query)
        match = re.fullmatch(r"/api/policies/([a-z0-9_]+)", path)
        if match and match.group(1) != "current" and method == "GET":
            result = self.policies.document(user, match.group(1))
        if path == "/api/manage/policies/readiness" and method == "GET":
            with self.app.db.connect() as c:
                require_role(c, user, "admin")
            result = self.policies.current(query, user)
        if path == "/api/manage/policies":
            if method == "GET":
                result = self.policies.listing(user, query)
            elif method == "POST":
                result = self.policies.save(user, h.body())
        match = re.fullmatch(r"/api/manage/policies/(policy_[a-z0-9]+)", path)
        if match and method == "PATCH":
            result = self.policies.save(user, h.body(), match.group(1))
        match = re.fullmatch(r"/api/manage/policies/(policy_[a-z0-9]+)/status", path)
        if match and method == "POST":
            result = self.policies.publish(user, match.group(1), h.body())
        if path == "/api/consents":
            if method == "GET":
                result = self.policies.consent_history(user, query)
            elif method == "POST":
                result = self.policies.consent(user, h.body())
        match = re.fullmatch(r"/api/checkout/orders/([a-z0-9_]+)/conditions", path)
        if match and method == "GET":
            result = self.policies.order_conditions(user, match.group(1))
        match = re.fullmatch(r"/api/manage/purchases/([a-z0-9_]+)/conditions", path)
        if match and method == "GET":
            result = self.policies.order_conditions(user, match.group(1), operator=True)
        match = re.fullmatch(r"/api/manage/access/([a-zA-Z0-9_-]+)", path)
        if match and method == "PATCH":
            result = self.accounts.update_grant(user, match.group(1), h.body())
        match = re.fullmatch(r"/api/media/([a-z0-9_]+)", path)
        if match and method == "GET":
            self.content.stream(h, user, match.group(1))
            return HANDLED
        if path == "/api/studio/courses":
            if method == "GET":
                result = self.content.courses(user)
            elif method == "POST":
                result = self.content.save_course(user, h.body())
        match = re.fullmatch(r"/api/studio/courses/([a-z0-9-]+)", path)
        if match:
            if method == "GET":
                result = self.content.courses(user, match.group(1))
            elif method == "PATCH":
                result = self.content.save_course(user, h.body(), match.group(1))
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/lessons(?:/([a-z0-9-]+))?", path
        )
        if match and method in ("POST", "PATCH"):
            result = self.content.save_lesson(
                user, match.group(1), h.body(), match.group(2)
            )
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/lessons/([a-z0-9-]+)/revisions", path
        )
        if match and method == "POST":
            result = self.revisions.create(user, *match.groups())
        match = re.fullmatch(
            r"/api/studio/courses/([a-z0-9-]+)/lessons/([a-z0-9-]+)/history(?:/(\d+))?",
            path,
        )
        if match and method == "GET":
            result = self.revisions.history(
                user,
                match.group(1),
                match.group(2),
                int(match.group(3)) if match.group(3) else None,
            )
        if path == "/api/studio/revisions" and method == "GET":
            result = self.revisions.list(user, query)
        match = re.fullmatch(r"/api/studio/revisions/([a-z0-9_]+)", path)
        if match and method == "GET":
            result = self.revisions.detail(user, match.group(1))
        if match and method == "PATCH":
            result = self.revisions.update(user, match.group(1), h.body())
        match = re.fullmatch(r"/api/studio/revisions/([a-z0-9_]+)/review", path)
        if match and method == "POST":
            result = self.revisions.review(user, match.group(1), h.body())
        match = re.fullmatch(r"/api/studio/courses/([a-z0-9-]+)/sections", path)
        if match and method == "POST":
            result = self.content.add_section(user, match.group(1), h.body())
        match = re.fullmatch(r"/api/studio/courses/([a-z0-9-]+)/assets", path)
        if match and method == "POST":
            result = self.content.upload(h, user, match.group(1))
        if path == "/api/qa/questions":
            if method == "GET":
                result = self.discussions.questions(user, query)
            elif method == "POST":
                result = self.discussions.create_question(user, h.body())
        if path == "/api/studio/questions" and method == "GET":
            result = self.discussions.questions(user, query, studio=True)
        match = re.fullmatch(r"/api/qa/questions/([a-z0-9_]+)", path)
        if match:
            if method == "GET":
                result = self.discussions.question_detail(user, match.group(1))
            elif method == "PATCH":
                data = h.body()
                if "action" not in data or data.get("action") == "edit":
                    data.pop("action", None)
                    result = self.discussion_api.edit(
                        user, "question", match.group(1), data
                    )
                else:
                    result = self.discussions.update_question(
                        user, match.group(1), data
                    )
        match = re.fullmatch(r"/api/qa/questions/([a-z0-9_]+)/answers", path)
        if match and method == "POST":
            result = self.discussions.answer(user, match.group(1), h.body())
        if path == "/api/community/posts":
            if method == "GET":
                result = self.discussions.list_posts(user, query)
            elif method == "POST":
                result = self.discussions.save_post(user, h.body())
        match = re.fullmatch(r"/api/community/posts/([a-z0-9_]+)", path)
        if match:
            if method == "GET":
                result = self.discussions.post_detail(user, match.group(1))
            elif method == "PATCH":
                result = self.discussions.save_post(user, h.body(), match.group(1))
        match = re.fullmatch(
            r"/api/community/posts/([a-z0-9_]+)/(comments|actions|reports)", path
        )
        if match and method == "POST":
            result = {
                "comments": self.discussions.comment,
                "actions": self.discussions.post_action,
                "reports": self.discussions.report,
            }[match.group(2)](user, match.group(1), h.body())
        if path == "/api/manage/reports" and method == "GET":
            result = self.discussions.reports(user)
        match = re.fullmatch(r"/api/community/comments/([a-z0-9_]+)", path)
        if match and method == "DELETE":
            result = self.discussions.delete_comment(user, match.group(1), h.body())
        match = re.fullmatch(r"/api/manage/reports/([a-z0-9_]+)", path)
        if match and method == "PATCH":
            result = self.discussions.resolve_report(user, match.group(1), h.body())
        if path == "/api/manage/users" and method == "GET":
            result = self.accounts.users(user, query)
        if path == "/api/studio/instructors" and method == "GET":
            result = self.accounts.instructors(user, query)
        match = re.fullmatch(r"/api/manage/users/([a-z0-9_-]+)", path)
        if match and method == "PATCH":
            result = self.accounts.update(user, match.group(1), h.body())
        if match and method == "GET":
            result = self.accounts.details(user, match.group(1))
        if path == "/api/manage/access" and method == "POST":
            result = self.accounts.access(user, h.body())
        if path == "/api/manage/audit" and method == "GET":
            result = self.accounts.audit(user, query)
        if path == "/api/notifications":
            if method == "GET":
                result = self.accounts.notifications(user)
            elif method == "PATCH":
                result = self.accounts.read_notifications(user, h.body())
        if path == "/api/checkout/config" and method == "GET":
            result = {"payment": self.commerce.config()}
        if path == "/api/checkout/orders":
            if method == "POST":
                result = self.commerce.create(user, h.body())
            elif method == "GET":
                result = self.commerce.orders(user)
        if path == "/api/checkout/confirm" and method == "POST":
            result = self.commerce.confirm(user, h.body())
        match = re.fullmatch(
            r"/api/checkout/preparations/([A-Za-z0-9_-]{12,120})", path
        )
        if match and method == "GET":
            result = self.commerce.preparation(user, match.group(1))
        match = re.fullmatch(r"/api/checkout/orders/([a-z0-9_]+)/preview", path)
        if match and method == "GET":
            result = self.commerce.preview(user, match.group(1))
        match = re.fullmatch(r"/api/checkout/orders/([a-z0-9_]+)/state", path)
        if match and method == "GET":
            result = self.commerce.authorization.state(user, match.group(1))
        match = re.fullmatch(r"/api/checkout/orders/([a-z0-9_]+)/authorization", path)
        if match and method == "POST":
            result = self.commerce.authorization.begin(user, match.group(1), h.body())
        match = re.fullmatch(
            r"/api/checkout/orders/([a-z0-9_]+)/close-authorization", path
        )
        if match and method == "POST":
            result = self.commerce.authorization.close(user, match.group(1), h.body())
        match = re.fullmatch(r"/api/checkout/orders/([a-z0-9_]+)/snapshot", path)
        if match and method == "GET":
            result = self.commerce.snapshot(user, match.group(1))
        match = re.fullmatch(r"/api/manage/purchases/([a-z0-9_]+)/snapshot", path)
        if match and method == "GET":
            result = self.commerce.snapshot(user, match.group(1), admin=True)
        match = re.fullmatch(r"/api/checkout/orders/([a-z0-9_]+)/reconcile", path)
        if match and method == "POST":
            result = self.commerce.reconcile(user, match.group(1))
        match = re.fullmatch(r"/api/checkout/orders/([a-z0-9_]+)/refund-request", path)
        if match and method == "POST":
            result = self.commerce.refund_request(user, match.group(1), h.body())
        match = re.fullmatch(r"/api/manage/purchases/([a-z0-9_]+)/reject-refund", path)
        if match and method == "POST":
            result = self.commerce.reject_refund(user, match.group(1), h.body())
        if path == "/api/manage/purchases" and method == "GET":
            result = self.commerce.orders(user, admin=True)
        match = re.fullmatch(r"/api/manage/purchases/([a-z0-9_]+)/refund", path)
        if match and method == "POST":
            result = self.commerce.refund(user, match.group(1), h.body())
        if path == "/api/watch" and method == "PUT":
            result = self.watch_progress.save(user, h.body())
        if result is not None:
            return 200, result, {}
        return None
