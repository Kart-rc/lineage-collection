package billing;

import java.util.List;

public class InvoiceController {
    private final InvoiceRepository invoices;

    public InvoiceController(InvoiceRepository invoices) {
        this.invoices = invoices;
    }

    public List<Invoice> byCurrency(String currency) {
        return invoices.findByCurrency(currency);
    }

    public Invoice save(Invoice invoice) {
        return invoices.save(invoice);
    }
}
