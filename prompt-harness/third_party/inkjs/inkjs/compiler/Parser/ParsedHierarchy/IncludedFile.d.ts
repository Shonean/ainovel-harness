import { ParsedObject } from "./Object";
import { InkObject as RuntimeObject } from "../../../engine/Object";
import { Story } from "./Story";
export declare class IncludedFile extends ParsedObject {
    readonly includedStory: Story | null;
    constructor(includedStory: Story | null);
    readonly GenerateRuntimeObject: () => RuntimeObject | null;
    get typeName(): string;
}
