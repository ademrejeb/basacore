export interface ApiErrorPayload {
  code: string;
  message: string;
  details?: unknown;
  request_id?: string;
}

export class BasacoreApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;
  readonly requestId?: string;

  constructor(status: number, payload: Partial<ApiErrorPayload> = {}) {
    super(payload.message ?? `Basacore API request failed with status ${status}`);
    this.name = "BasacoreApiError";
    this.status = status;
    this.code = payload.code ?? "request_failed";
    this.details = payload.details;
    this.requestId = payload.request_id;
  }
}

export class BasacoreConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BasacoreConfigurationError";
  }
}
