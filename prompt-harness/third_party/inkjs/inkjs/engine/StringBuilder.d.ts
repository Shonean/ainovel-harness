export declare class StringBuilder {
    private string;
    constructor(str?: string);
    get Length(): number;
    Append(str: string | null): void;
    AppendLine(str?: string): void;
    AppendFormat(format: string, ...args: any[]): void;
    toString(): string;
    Clear(): void;
}
