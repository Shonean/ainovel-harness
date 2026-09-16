export type ErrorHandler = (message: string, type: ErrorType) => void;
export declare enum ErrorType {
    Author = 0,
    Warning = 1,
    Error = 2
}
