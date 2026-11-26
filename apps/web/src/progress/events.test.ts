import { afterEach, describe, expect, it, vi } from "vitest";
import { subscribeProject } from "./events";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  handlers = new Map<string, EventListenerOrEventListenerObject>();
  onopen: ((this: EventSource, ev: Event) => unknown) | null = null;
  onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null = null;

  constructor() {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.handlers.set(type, listener);
  }

  close() {}

  dispatch(type: string) {
    const listener = this.handlers.get(type);
    if (typeof listener === "function") listener(new Event(type));
    else listener?.handleEvent(new Event(type));
  }
}

describe("project event subscriptions", () => {
  afterEach(() => {
    FakeEventSource.instances = [];
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it.each(["evaluation_protocol_revised", "goal_contract_revised"])(
    "refreshes subscribers for %s",
    (eventName) => {
      vi.useFakeTimers();
      vi.stubGlobal("EventSource", FakeEventSource);
      const refresh = vi.fn();
      const unsubscribe = subscribeProject("project-1", refresh);

      FakeEventSource.instances[0].dispatch(eventName);
      vi.advanceTimersByTime(150);

      expect(refresh).toHaveBeenCalledOnce();
      unsubscribe();
    },
  );
});
