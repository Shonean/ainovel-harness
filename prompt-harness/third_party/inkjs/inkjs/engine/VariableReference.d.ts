import { InkObject } from "./Object";
import { Path } from "./Path";
export declare class VariableReference extends InkObject {
    name: string | null;
    pathForCount: Path | null;
    get containerForCount(): import("./Container").Container | null;
    get pathStringForCount(): string | null;
    set pathStringForCount(value: string | null);
    constructor(name?: string | null);
    toString(): string;
}
