package billing;

import jakarta.persistence.*;

@Entity
@Table(name = "invoices")
public class Invoice {
    private Integer id;

    @Column(name = "account_ref")
    private String accountRef;

    private String currency;
}
