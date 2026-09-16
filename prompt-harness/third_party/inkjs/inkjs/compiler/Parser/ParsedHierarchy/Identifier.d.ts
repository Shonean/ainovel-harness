import { DebugMetadata } from "../../../engine/DebugMetadata";
export declare class Identifier {
    name: string;
    debugMetadata: DebugMetadata | null;
    constructor(name: string);
    get typeName(): string;
    static Done(): Identifier;
    readonly toString: () => string;
}
