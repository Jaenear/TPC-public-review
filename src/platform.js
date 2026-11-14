import { renderDiscussions } from "./discussions.js";
import { renderStudio } from "./studio.js";
import { renderOperations } from "./operations.js";
import { renderCommerce } from "./commerce.js";
import { renderRevisions } from "./revisions.js";
import { renderPolicies } from "./policies.js";
import { renderPricing } from "./pricing.js";
import { renderContracts } from "./contracts.js";
import { renderCoachStudio } from "./coach-studio.js";
import { renderCurriculum } from "./curriculum.js";
import { renderResources } from "./resources.js";
import { renderPublications } from "./publications.js";
import { renderMediaLibrary } from "./media-library.js";
import { renderLearningStart } from "./learning-start.js";
import { renderCaptionPage } from "./captions.js";
import { renderModeration } from "./moderation.js";
import { renderReconciliation } from "./reconciliation.js";
import { renderCoaching } from "./coaching.js";
import { renderReservations } from "./reservation.js";
import { renderHandReviews } from "./hand-reviews.js";

export async function renderPlatformPage(path, params, ctx) {
  return (
    (await renderHandReviews(path, params, ctx)) ||
    (await renderReservations(path, params, ctx)) ||
    (await renderCoaching(path, params, ctx)) ||
    (await renderReconciliation(path, params, ctx)) ||
    (await renderModeration(path, params, ctx)) ||
    (await renderLearningStart(path, ctx)) ||
    (await renderCaptionPage(path, params, ctx)) ||
    (await renderMediaLibrary(path, params, ctx)) ||
    (await renderPublications(path, params, ctx)) ||
    (await renderCurriculum(path, params, ctx)) ||
    (await renderResources(path, params, ctx)) ||
    (await renderPricing(path, params, ctx)) ||
    (await renderContracts(path, params, ctx)) ||
    (await renderCoachStudio(path, params, ctx)) ||
    (await renderPolicies(path, params, ctx)) ||
    (await renderRevisions(path, params, ctx)) ||
    (await renderDiscussions(path, params, ctx)) ||
    (await renderStudio(path, params, ctx)) ||
    (await renderOperations(path, params, ctx)) ||
    (await renderCommerce(path, params, ctx))
  );
}
