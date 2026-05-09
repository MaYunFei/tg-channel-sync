(() => {
  class TgcsApiError extends Error {
    constructor(message, details = {}) {
      super(message || "请求失败");
      this.name = "TgcsApiError";
      this.details = details;
    }
  }

  function createErrorPayload(message, extras = {}) {
    return {
      status: "error",
      message: message || "请求失败",
      ...extras,
    };
  }

  async function requestJson(url, options = {}) {
    const {
      method = "GET",
      json,
      form,
      headers = {},
      cache,
    } = options;

    const requestHeaders = { ...headers };
    const init = { method, headers: requestHeaders };
    if (cache !== undefined) init.cache = cache;

    if (json !== undefined) {
      requestHeaders["Content-Type"] = requestHeaders["Content-Type"] || "application/json";
      init.body = JSON.stringify(json);
    } else if (form !== undefined) {
      init.body = form;
    }

    let response;
    try {
      response = await fetch(url, init);
    } catch (error) {
      return createErrorPayload("无法连接到后端", {
        network_error: true,
        original_message: error && error.message ? error.message : "Failed to fetch",
      });
    }

    let payload = {};
    try {
      payload = await response.json();
    } catch (_) {}

    if (!response.ok) {
      return createErrorPayload(payload.message || `请求失败 (${response.status})`, {
        http_status: response.status,
        ...payload,
      });
    }
    return payload;
  }

  function requestRaw(url, options = {}) {
    return fetch(url, options);
  }

  function ensureSuccess(payload, fallbackMessage = "请求失败") {
    if (payload && payload.status !== "error") return payload;
    throw new TgcsApiError((payload && payload.message) || fallbackMessage, payload || {});
  }

  function getErrorMessage(error, fallbackMessage = "请求失败") {
    if (error instanceof TgcsApiError) return error.message || fallbackMessage;
    if (error && typeof error.message === "string" && error.message) return error.message;
    return fallbackMessage;
  }

  function buildFormData(values, options = {}) {
    const { valueTransform } = options;
    const form = new FormData();
    Object.keys(values || {}).forEach((key) => {
      const rawValue = values[key];
      const value = valueTransform ? valueTransform(rawValue, key) : rawValue;
      form.append(key, value);
    });
    return form;
  }

  window.TgcsApi = {
    TgcsApiError,
    requestJson,
    requestRaw,
    ensureSuccess,
    getErrorMessage,
    buildFormData,
    getJson(url, options = {}) {
      return requestJson(url, { ...options, method: "GET" });
    },
    postJson(url, json, options = {}) {
      return requestJson(url, { ...options, method: "POST", json });
    },
    postForm(url, form, options = {}) {
      return requestJson(url, { ...options, method: "POST", form });
    },
    deleteJson(url, options = {}) {
      return requestJson(url, { ...options, method: "DELETE" });
    },
  };
})();
