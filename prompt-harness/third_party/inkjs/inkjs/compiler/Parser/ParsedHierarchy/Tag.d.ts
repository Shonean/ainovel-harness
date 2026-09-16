import { ParsedObject } from "./Object";
import { InkObject as RuntimeObject } from "../../../engine/Object";
export declare class Tag extends ParsedObject {
    isStart: boolean;
    inChoice: boolean;
    constructor(isStart: boolean, inChoice?: boolean);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    readonly toString: () => "#StartTag" | "#EndTag";
}
import { Tag as RuntimeTag } from "../../../engine/Tag";
import { Wrap } from "./Wrap";
export declare class LegacyTag extends Wrap<RuntimeTag> {
    constructor(tag: RuntimeTag);
    get typeName(): string;
}
