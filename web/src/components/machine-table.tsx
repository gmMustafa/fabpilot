/**
 * All machines as a plain table (7.1): proves the typed client end to end. The real plant
 * overview with live tiles comes in 7.2.
 */
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";

export async function MachineTable() {
  let result;
  try {
    result = await api.GET("/machines");
  } catch {
    return <p className="text-sm text-muted-foreground">Machines unavailable: API unreachable.</p>;
  }
  const { data, error, response } = result;
  if (!data) {
    const message = describeError(response.status, error, response.headers.get("Retry-After"));
    return <p className="text-sm text-destructive">Machines unavailable: {message}</p>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Code</TableHead>
          <TableHead>Name</TableHead>
          <TableHead>Line</TableHead>
          <TableHead>Type</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Last reading</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((m) => (
          <TableRow key={m.code}>
            <TableCell className="font-mono">{m.code}</TableCell>
            <TableCell>{m.name}</TableCell>
            <TableCell>{m.line}</TableCell>
            <TableCell>{m.type.replace("_", " ")}</TableCell>
            <TableCell>
              <Badge variant={m.status === "running" ? "secondary" : "outline"}>{m.status}</Badge>
            </TableCell>
            <TableCell className="text-muted-foreground">
              {m.last_reading_at ? new Date(m.last_reading_at).toLocaleString() : "no data"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
