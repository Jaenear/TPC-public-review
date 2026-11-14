let sdkPromise;

export function loadToss() {
  if (typeof window.TossPayments === "function")
    return Promise.resolve(window.TossPayments);
  if (!sdkPromise)
    sdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      let settled = false;
      const timeout = setTimeout(() => fail(), 20000);
      script.src = "https://js.tosspayments.com/v2/standard";
      const fail = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        sdkPromise = null;
        script.remove();
        reject(
          new Error(
            "결제수단을 불러오지 못했습니다. 연결을 확인하고 직접 다시 시도해주세요.",
          ),
        );
      };
      script.onerror = fail;
      script.onload = () => {
        if (settled) return;
        if (typeof window.TossPayments !== "function") return fail();
        settled = true;
        clearTimeout(timeout);
        resolve(window.TossPayments);
      };
      document.head.append(script);
    });
  return sdkPromise;
}

/** Unique selectors and late destruction keep a detached checkout from binding a new account's page. */
export async function mountCheckoutSDK({
  root,
  payment,
  order,
  ownerId,
  active,
  load = loadToss,
  nonce = crypto.randomUUID(),
}) {
  const controls = [];
  let disposed = false;
  const destroy = async () => {
    disposed = true;
    await Promise.allSettled(
      controls
        .splice(0)
        .map((control) => Promise.resolve().then(() => control?.destroy())),
    );
  };
  const check = () => {
    if (disposed || !active())
      throw new Error(
        "주문 화면이 변경됐습니다. 현재 계정에서 다시 확인해주세요.",
      );
  };
  async function retain(promise) {
    const control = await promise;
    controls.push(control);
    check();
    return control;
  }
  try {
    const Toss = await load();
    check();
    const widgets = Toss(payment.clientKey).widgets({ customerKey: ownerId });
    await widgets.setAmount({ currency: "KRW", value: order.amount });
    check();
    const methods = root.querySelector("[data-toss-methods]"),
      agreements = root.querySelector("[data-toss-agreements]");
    methods.id = "tpc-methods-" + nonce;
    agreements.id = "tpc-agreements-" + nonce;
    await retain(
      widgets.renderPaymentMethods({
        selector: "#" + methods.id,
        variantKey: "DEFAULT",
      }),
    );
    await retain(
      widgets.renderAgreement({
        selector: "#" + agreements.id,
        variantKey: "AGREEMENT",
      }),
    );
    return {
      clientKey: payment.clientKey,
      destroy,
      request: () => {
        check();
        return widgets.requestPayment({
          orderId: order.id,
          orderName: order.title,
          successUrl:
            location.origin +
            "/?paymentResult=success&tpcOrderId=" +
            encodeURIComponent(order.id),
          failUrl:
            location.origin +
            "/?paymentResult=fail&tpcOrderId=" +
            encodeURIComponent(order.id),
        });
      },
    };
  } catch (error) {
    await destroy();
    throw error;
  }
}
