package petclinic;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;

@Entity
@Table(name = "visits")
public class Visit {
    private Integer id;

    @Column(name = "visit_date")
    private String visitDate;
}
