import { InkObject } from "./Object";
import { Path } from "./Path";
import { Container } from "./Container";
export declare class ChoicePoint extends InkObject {
    _pathOnChoice: Path | null;
    hasCondition: boolean;
    hasStartContent: boolean;
    hasChoiceOnlyContent: boolean;
    isInvisibleDefault: boolean;
    onceOnly: boolean;
    constructor(onceOnly?: boolean);
    get pathOnChoice(): Path | null;
    set pathOnChoice(value: Path | null);
    get choiceTarget(): Container | null;
    get pathStringOnChoice(): string;
    set pathStringOnChoice(value: string);
    get flags(): number;
    set flags(value: number);
    toString(): string;
}
