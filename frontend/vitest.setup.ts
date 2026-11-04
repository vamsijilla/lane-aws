import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount rendered components between tests (Testing Library only does this with globals on).
afterEach(cleanup);
