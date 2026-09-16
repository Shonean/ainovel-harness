import { ContentList } from "../ContentList";
import { Divert as RuntimeDivert } from "../../../../engine/Divert";
import { ParsedObject } from "../Object";
import { InkObject as RuntimeObject } from "../../../../engine/Object";
import { SequenceType } from "./SequenceType";
import { Story } from "../Story";
export declare class Sequence extends ParsedObject {
    readonly sequenceType: SequenceType;
    private _sequenceDivertsToResolve;
    sequenceElements: ParsedObject[];
    constructor(elementContentLists: ContentList[], sequenceType: SequenceType);
    get typeName(): string;
    readonly GenerateRuntimeObject: () => RuntimeObject;
    readonly AddDivertToResolve: (divert: RuntimeDivert, targetContent: RuntimeObject) => void;
    ResolveReferences(context: Story): void;
}
