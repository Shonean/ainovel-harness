import { Identifier } from "./Identifier";
export declare class Argument {
    identifier: Identifier | null;
    isByReference: boolean | null;
    isDivertTarget: boolean | null;
    constructor(identifier?: Identifier | null, isByReference?: boolean | null, isDivertTarget?: boolean | null);
    get typeName(): string;
}
