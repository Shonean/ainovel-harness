import { INamedContent } from "./INamedContent";
export declare function asOrNull<T>(obj: any, type: (new (...arg: any[]) => T) | (Function & {
    prototype: T;
})): T | null;
export declare function asOrThrows<T>(obj: any, type: (new (...arg: any[]) => T) | (Function & {
    prototype: T;
})): T | never;
export declare function asNumberOrThrows(obj: any): number;
export declare function asBooleanOrThrows(obj: any): boolean;
export declare function asINamedContentOrNull(obj: any): INamedContent | null;
export declare function nullIfUndefined<T>(obj: T | undefined): T | null;
export declare function isEquatable(type: any): boolean;
export declare function filterUndef<T>(element: T | undefined): element is T;
