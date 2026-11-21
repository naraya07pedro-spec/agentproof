import { createForwarder } from "../../forward";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const forward = createForwarder("v2");

export const GET = forward;
export const POST = forward;
